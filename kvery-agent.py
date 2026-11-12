"""HTTP agent that runs SQL statements on the databases named in its configuration file.

The caller sends a JSON Web Token to /execute: in the Authorization header (Bearer) of a GET request, or as the body
of a POST request, which has room for a long statement. The token is signed with the shared secret of the
configuration (HS256); its payload names a configured connection ("conn") and carries one SQL statement ("sql"). A
token with an expiry claim ("exp") is refused once it has expired, and a token that also carries a one-time ID
("jti") is accepted once only. Only callers on the IP whitelist are served: any other address gets the same refusal
on every path.

The answer is a JSON object: {"status": 1, "response": ...} on success ("response" holds the rows of a statement
that returns rows, with the column order in "columns"; otherwise 1 when rows were affected and 0 when none were, and
for an INSERT the last inserted ID in "last_inserted_id"), and {"status": 0, "response": message} on failure.
"""
import base64
import datetime
import json
import logging
import sys
import threading
import time
from logging.handlers import TimedRotatingFileHandler
from urllib.parse import unquote

import jwt
from flask import Flask, jsonify, request
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import NullPool
from werkzeug.exceptions import HTTPException

app = Flask(__name__)

# The configuration file: the first command-line argument when the agent is started as a program, otherwise (and
# without an argument) config.json of the working directory
DEFAULT_CONFIG_PATH = 'config.json'
CONFIG_PATH = sys.argv[1] if __name__ == '__main__' and len(sys.argv) > 1 else DEFAULT_CONFIG_PATH
REQUIRED_KEYS = ('connections', 'port', 'ip_whitelist', 'log_enabled', 'logrotate_days', 'log_filename', 'secret_key')
CONNECTION_KEYS = ('type', 'host', 'port', 'username', 'password', 'database')

# SQLAlchemy driver of each connection type of the configuration
DRIVERS = {'mysql': 'mysql+pymysql', 'pgsql': 'postgresql+psycopg2', 'dblib': 'mssql+pyodbc'}
DEFAULT_ODBC_DRIVER = 'ODBC Driver 17 for SQL Server'

# A secret shorter than the smallest length is refused at start ('min_secret_length' can raise that length, never
# lower it); a secret shorter than the recommended length is reported in the log
MIN_SECRET_LENGTH = 16
RECOMMENDED_SECRET_LENGTH = 32

# Signing algorithms a token may use ('algorithms' selects among them): the ones that sign with the shared secret
SUPPORTED_ALGORITHMS = ('HS256', 'HS384', 'HS512')
DEFAULT_ALGORITHMS = ['HS256']

# How date and date-time values are written into the answer: 'http' = HTTP date text (e.g.
# "Thu, 02 Jan 2025 00:00:00 GMT"), 'iso' = ISO 8601 text with a space between the date and the time (e.g.
# "2025-01-02 00:00:00")
DATE_FORMATS = ('http', 'iso')
DEFAULT_DATE_FORMAT = 'http'

LOG_LEVELS = ('DEBUG', 'INFO', 'WARNING', 'ERROR')
DEFAULT_LOG_LEVEL = 'INFO'

# Optional keys that hold a whole number, each with its default and its smallest allowed value:
#   threads            statements the waitress server runs at the same time; further requests wait for a free thread
#   connection_limit   sockets the waitress server keeps open at the same time, its own two included
#   max_header_bytes   largest request header the waitress server takes
#   max_body_bytes     largest request body that is read (the token of a POST request)
#   expiry_leeway      seconds a token is still taken after its expiry time, for a clock that differs from the caller's
#   min_secret_length  smallest length of the secret
NUMBER_KEYS = {
    'threads': (100, 1),
    'connection_limit': (100, 1),
    'max_header_bytes': (262144, 1),
    'max_body_bytes': (16 * 1024 * 1024, 1),
    'expiry_leeway': (0, 0),
    'min_secret_length': (MIN_SECRET_LENGTH, MIN_SECRET_LENGTH),
}


def number_setting(config, key):
    """The whole number of an optional key of NUMBER_KEYS, or its default when the key is missing. Raises ValueError
    when the value is not a whole number or is below the smallest allowed value."""
    default, minimum = NUMBER_KEYS[key]
    value = config.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"The '{key}' key of the configuration file must be a whole number of at least {minimum}.")
    return value


def validate_config(config):
    """Raises ValueError when the configuration lacks a required key or holds a value that cannot be used."""
    if not isinstance(config, dict):
        raise ValueError("The configuration file must hold a JSON object.")
    for key in REQUIRED_KEYS:
        if key not in config:
            raise ValueError(f"Missing '{key}' key in the configuration file.")
    if not isinstance(config['connections'], dict):
        raise ValueError("The 'connections' key of the configuration file must be an object.")
    for name, connection in config['connections'].items():
        if not isinstance(connection, dict) or not all(key in connection for key in CONNECTION_KEYS):
            raise ValueError(f"Missing one or more required data under '{name}' key.")
    if not isinstance(config['ip_whitelist'], list):
        raise ValueError("The 'ip_whitelist' key of the configuration file must be a list of IP addresses.")
    for key in NUMBER_KEYS:
        number_setting(config, key)
    secret = config['secret_key']
    min_secret_length = number_setting(config, 'min_secret_length')
    if not isinstance(secret, str) or len(secret) < min_secret_length:
        raise ValueError(
            f"The 'secret_key' of the configuration file must be a text of at least {min_secret_length} characters "
            f"({RECOMMENDED_SECRET_LENGTH} or more random characters are recommended)."
        )
    algorithms = config.get('algorithms', DEFAULT_ALGORITHMS)
    if not (isinstance(algorithms, list) and algorithms and all(name in SUPPORTED_ALGORITHMS for name in algorithms)):
        raise ValueError(
            "The 'algorithms' key of the configuration file must be a list of one or more of: "
            + ', '.join(SUPPORTED_ALGORITHMS) + '.'
        )
    if config.get('date_format', DEFAULT_DATE_FORMAT) not in DATE_FORMATS:
        raise ValueError("The 'date_format' key of the configuration file must be 'http' or 'iso'.")
    if config.get('log_level', DEFAULT_LOG_LEVEL) not in LOG_LEVELS:
        raise ValueError("The 'log_level' key of the configuration file must be one of: " + ', '.join(LOG_LEVELS) + '.')
    if not isinstance(config.get('trusted_proxies', []), list):
        raise ValueError("The 'trusted_proxies' key of the configuration file must be a list of IP addresses.")
    if not isinstance(config.get('host', ''), str):
        raise ValueError("The 'host' key of the configuration file must be an IP address.")


def load_config(config_path):
    """Returns the validated configuration of the JSON file; ends the program when it cannot be used."""
    try:
        with open(config_path, 'r') as config_file:
            loaded = json.load(config_file)
        validate_config(loaded)
        return loaded
    except FileNotFoundError:
        logging.error(f"File {config_path} not found.")
    except json.JSONDecodeError as error:
        logging.error(f"Error while parsing {config_path}: {error}")
    except ValueError as error:
        logging.error(error)
    sys.exit(1)


config = load_config(CONFIG_PATH)

# Log file with daily rotation and console output at the level of 'log_level', or no logging at all. Statements and
# result rows are not logged at any level: the database library stays at warning level, and the text of a statement
# is logged only with 'log_sql'.
if config.get('log_enabled'):
    logging.basicConfig(level=getattr(logging, config.get('log_level', DEFAULT_LOG_LEVEL)),
                        format='%(asctime)s - %(levelname)s - %(message)s',
                        handlers=[
                            TimedRotatingFileHandler(config['log_filename'], when='D', interval=1,
                                                     backupCount=config['logrotate_days']),
                            logging.StreamHandler(sys.stdout)
                        ])
    logging.getLogger('sqlalchemy').setLevel(logging.WARNING)
else:
    logging.disable(logging.CRITICAL)

logging.info(f"Successfully loaded '{CONFIG_PATH}' configuration file.")
if len(config['secret_key']) < RECOMMENDED_SECRET_LENGTH:
    logging.warning(f"The 'secret_key' is shorter than the recommended {RECOMMENDED_SECRET_LENGTH} characters.")

secret_key = config['secret_key']
date_format = config.get('date_format', DEFAULT_DATE_FORMAT)
app.config['MAX_CONTENT_LENGTH'] = number_setting(config, 'max_body_bytes')

# How a token is checked: the algorithms it may be signed with, the seconds it is still taken after its expiry time,
# and, with 'require_expiry', the claims it has to carry (an expiry time and a one-time ID, so every request can be
# run once only)
token_algorithms = config.get('algorithms', DEFAULT_ALGORITHMS)
expiry_leeway = number_setting(config, 'expiry_leeway')
TOKEN_OPTIONS = {'require': ['exp', 'jti']} if config.get('require_expiry') else {}


class UsedTokens:
    """The one-time IDs of the tokens that were accepted. An ID is kept until its token expires: after that the
    token is refused for its expiry."""

    CLEANUP_SECONDS = 60

    def __init__(self):
        """Starts without recorded IDs."""
        self._expiry = {}
        self._lock = threading.Lock()
        self._cleaned = time.time()

    def first_use(self, token_id, expires):
        """Records the ID of a token that expires at the given time (seconds since the epoch). False when a token
        with this ID that has not expired was recorded before."""
        now = time.time()
        with self._lock:
            if now - self._cleaned >= self.CLEANUP_SECONDS:
                self._expiry = {key: until for key, until in self._expiry.items() if until >= now}
                self._cleaned = now
            if self._expiry.get(token_id, 0) >= now:
                return False
            self._expiry[token_id] = expires
            return True


used_tokens = UsedTokens()

# One engine per configured connection, created at its first use. An engine keeps no open connections (NullPool):
# every request gets a connection of its own and closes it, so no session state passes from one request to another.
engines = {}
engines_lock = threading.Lock()


def connection_url(driver, connection):
    """The SQLAlchemy URL of a configured connection, as text.

    The values are joined as they are written in the configuration, so they are read as parts of a URL: the user name
    and the password are percent-decoded, and driver options may follow the database name ("name?option=value"). Only
    an "@" of the password is encoded here, because it would end the password. For SQL Server the values are joined
    into an ODBC connection string, where options may follow the database name too ("name;Option=value").
    """
    if driver == DRIVERS['dblib']:
        odbc_driver = connection.get('odbc_driver', DEFAULT_ODBC_DRIVER)
        return (
            f"{driver}:///?odbc_connect=DRIVER={{{odbc_driver}}};SERVER={connection['host']},{connection['port']};"
            f"DATABASE={connection['database']};UID={connection['username']};PWD={connection['password']}"
        )
    password = str(connection['password']).replace('@', '%40')
    return (
        f"{driver}://{connection['username']}:{password}"
        f"@{connection['host']}:{connection['port']}/{connection['database']}"
    )


def get_engine(name):
    """The engine of a configured connection, or None when the name or its database type is not known."""
    connection = config['connections'].get(name)
    if not isinstance(connection, dict):
        return None
    driver = DRIVERS.get(connection['type'])
    if driver is None:
        return None
    with engines_lock:
        if name not in engines:
            # no_parameters: a statement is passed to the database driver without a parameter list, so the driver
            # reads no character of it (a percent sign, a colon) as a parameter marker
            engines[name] = create_engine(
                connection_url(driver, connection),
                poolclass=NullPool,
                execution_options={'no_parameters': True},
            )
        return engines[name]


def last_inserted_id(connection, dialect_name):
    """The ID the last INSERT of the connection generated, or 0 when it generated none."""
    if dialect_name == 'mysql':
        return connection.exec_driver_sql('SELECT LAST_INSERT_ID()').scalar()
    if dialect_name == 'postgresql':
        # lastval() fails when the session has used no sequence. The savepoint keeps that failure from ending the
        # transaction of the INSERT.
        savepoint = connection.begin_nested()
        try:
            value = connection.exec_driver_sql('SELECT lastval()').scalar()
            savepoint.commit()
            return value
        except SQLAlchemyError:
            savepoint.rollback()
            return 0
    if dialect_name == 'mssql':
        return connection.exec_driver_sql('SELECT @@IDENTITY AS last_insert_id').scalar()
    return 0


def duration_text(value):
    """A time span as the text of a TIME value: [-]HH:MM:SS, with the fraction of a second when there is one."""
    microseconds_total = value // datetime.timedelta(microseconds=1)
    sign = '-' if microseconds_total < 0 else ''
    seconds_total, microseconds = divmod(abs(microseconds_total), 1000000)
    hours, rest = divmod(seconds_total, 3600)
    minutes, seconds = divmod(rest, 60)
    text = f"{sign}{hours:02d}:{minutes:02d}:{seconds:02d}"
    return text + (f".{microseconds:06d}" if microseconds else '')


def json_value(value):
    """A value of a result row in a form that can be written into the JSON answer.

    Dates follow the 'date_format' of the configuration (in the 'http' format they are left to the JSON encoder),
    times and time spans become text, binary data becomes text when it is valid UTF-8 and Base64 text otherwise, lists
    and objects are converted value by value. Numbers and text stay as they are; any other type (a decimal number, a
    UUID) becomes its text.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, datetime.datetime):
        return value.isoformat(sep=' ') if date_format == 'iso' else value
    if isinstance(value, datetime.date):
        return value.isoformat() if date_format == 'iso' else value
    if isinstance(value, datetime.time):
        return value.isoformat()
    if isinstance(value, datetime.timedelta):
        return duration_text(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        data = bytes(value)
        try:
            return data.decode('utf-8')
        except UnicodeDecodeError:
            return base64.b64encode(data).decode('ascii')
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    return str(value)


def error_summary(error):
    """The kind of a database error and its code (a number or an SQLSTATE) when the driver gave one. It holds no part
    of the driver's message, which can quote the statement."""
    database_error = getattr(error, 'orig', None)
    if database_error is None:
        return type(error).__name__
    for code in (getattr(database_error, 'pgcode', None), *database_error.args[:1]):
        is_number = isinstance(code, int) and not isinstance(code, bool)
        is_sqlstate = isinstance(code, str) and len(code) == 5 and code.isalnum()
        if is_number or is_sqlstate:
            return f"{type(database_error).__name__} ({code})"
    return type(database_error).__name__


def without_password(text, connection):
    """A message with the password of a configured connection masked, both as it is written in the configuration
    and percent-decoded (the form the database gets)."""
    password = str(connection['password'])
    for secret in {password, unquote(password)}:
        if secret:
            text = text.replace(secret, '********')
    return text


def client_address():
    """The address of the caller: the peer of the connection, or, when that peer is one of the 'trusted_proxies' of
    the configuration, the last address of the X-Forwarded-For header that is not such a proxy."""
    address = request.remote_addr
    proxies = config.get('trusted_proxies', [])
    if address in proxies:
        forwarded = [part.strip() for part in request.headers.get('X-Forwarded-For', '').split(',')]
        for candidate in reversed(forwarded):
            if candidate and candidate not in proxies:
                return candidate
    return address


def request_token():
    """The token of the request: the Bearer value of the Authorization header, or, for a POST request without that
    header, the request body. None when the request carries none."""
    header = request.headers.get('Authorization')
    if header:
        return header[len('Bearer '):].strip() if header.startswith('Bearer ') else None
    if request.method == 'POST':
        return request.get_data(as_text=True).strip() or None
    return None


def failure(message, http_status):
    """The JSON answer of a failed request."""
    return jsonify({'status': 0, 'response': message}), http_status


@app.before_request
def refuse_unlisted_address():
    """Refuses every request of an address that is not on the IP whitelist, whatever path or method it asks for."""
    address = client_address()
    if address not in config['ip_whitelist']:
        logging.error(f"Unauthorized IP address: {address}")
        return failure('Unauthorized IP address', 403)
    return None


@app.errorhandler(HTTPException)
def http_error(error):
    """An HTTP error of the framework (unknown path, method that is not allowed) as a JSON answer."""
    return failure(error.description, error.code)


@app.route('/execute', methods=['GET', 'POST'])
def execute_query():
    """Runs the statement of a signed request on the connection it names and answers with the result."""
    token = request_token()
    if token is None:
        logging.error("Authorization header missing or invalid.")
        return failure('Authorization header missing or invalid', 401)

    try:
        decoded_token = jwt.decode(token, secret_key, algorithms=token_algorithms, leeway=expiry_leeway,
                                   options=TOKEN_OPTIONS)
    except jwt.ExpiredSignatureError:
        logging.error("Token expired.")
        return failure('Token expired', 401)
    except jwt.InvalidTokenError as error:
        logging.error(f"Invalid token: {error}")
        return failure(str(error), 401)

    # A token with an expiry time and a one-time ID is accepted once: the ID is recorded before the statement runs,
    # so the same token cannot pass again, not even in a request that arrives at the same time. The ID is kept for
    # as long as the token is taken (its expiry time and the leeway).
    token_id, expires = decoded_token.get('jti'), decoded_token.get('exp')
    taken_until = float(expires) + expiry_leeway if expires is not None else None
    if token_id is not None and taken_until is not None and not used_tokens.first_use(str(token_id), taken_until):
        logging.error("Token already used.")
        return failure('Token already used', 401)

    conn = decoded_token.get('conn')
    sql = decoded_token.get('sql')
    if not isinstance(conn, str) or not isinstance(sql, str) or not conn or not sql:
        logging.error("Connection and SQL query are required.")
        return failure('Connection and SQL query are required', 400)

    logging.info(f"Statement of {len(sql)} characters for connection '{conn}'.")
    if config.get('log_sql'):
        logging.info(f"SQL: '{sql}'")

    try:
        engine = get_engine(conn)
    except ImportError as error:
        logging.error(f"The database driver of connection '{conn}' cannot be loaded: {error}")
        return failure('The database driver of the connection is not installed', 500)
    except Exception as error:
        # The message of such an error can quote the connection URL with its password: only its kind is logged
        logging.error(f"The connection '{conn}' cannot be set up: {type(error).__name__}")
        return failure('Internal error', 500)
    if engine is None:
        logging.error(f"Invalid connection or database configuration not found for '{conn}'.")
        return failure('Invalid connection or database configuration not found', 404)

    connection = None
    try:
        connection = engine.connect()
        # The statement runs in a transaction of its own: committed when it succeeds, rolled back when it fails
        with connection, connection.begin():
            # The statement is sent to the database as it is (see get_engine())
            result = connection.exec_driver_sql(sql)
            if result.returns_rows:
                columns = list(result.keys())
                rows = [
                    {key: json_value(value) for key, value in row._mapping.items()}
                    for row in result.fetchall()
                ]
                answer = {'status': 1, 'response': rows, 'columns': columns}
            else:
                logging.info(f"Rows affected: {result.rowcount}")
                answer = {'status': 1, 'response': 1 if result.rowcount > 0 else 0}
                if sql.strip().lower().startswith('insert'):
                    answer['last_inserted_id'] = json_value(last_inserted_id(connection, engine.dialect.name))
                    logging.info(f"Last inserted ID: {answer['last_inserted_id']}")
        return jsonify(answer)
    except SQLAlchemyError as error:
        if connection is None:
            # No statement was sent: the message of the failed connection is logged and answered in full, with the
            # password of the connection masked in case the database driver quotes it
            message = without_password(str(error), config['connections'][conn])
            logging.error(f"Database connection failed for '{conn}': {' '.join(message.split())}")
            return failure(message, 500)
        # The caller, who sent the statement, gets the full message. That message can quote the statement and its
        # data, so the log gets the kind and the code of the error only, unless statements are logged.
        if config.get('log_sql'):
            logging.error(f"Error while executing SQL query: {' '.join(str(error).split())}")
        else:
            logging.error(f"Error while executing SQL query: {error_summary(error)}")
        return failure(str(error), 500)
    except Exception:
        logging.exception("Unexpected error while handling a request.")
        return failure('Internal error', 500)


if __name__ == '__main__':
    host = config.get('host') or '0.0.0.0'
    port = config['port']
    try:
        from waitress import serve
    except ImportError:
        logging.warning("The 'waitress' package is not installed: the built-in development server of Flask is used.")
        app.run(host=host, port=port, debug=False)
    else:
        # The forwarding headers are passed on as they arrive: client_address() decides whether they count
        serve(app, host=host, port=port, threads=number_setting(config, 'threads'),
              connection_limit=number_setting(config, 'connection_limit'),
              max_request_header_size=number_setting(config, 'max_header_bytes'),
              max_request_body_size=number_setting(config, 'max_body_bytes'), clear_untrusted_proxy_headers=False)
