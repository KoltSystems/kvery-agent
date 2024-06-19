import os
import sys
import logging
from logging.handlers import TimedRotatingFileHandler
from flask import Flask, request, jsonify
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
import json
import jwt
import pyodbc

app = Flask(__name__)

# Function to load configuration from a JSON file
def load_config(config_path):
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
            validate_config(config)
            return config
    except FileNotFoundError:
        logging.error(f"File {config_path} not found.")
        sys.exit(1)  # Exit if config file is not found
    except json.JSONDecodeError as e:
        logging.error(f"Error while parsing {config_path}: {e}")
        sys.exit(1)  # Exit if JSON is invalid
    except ValueError as ve:
        logging.error(ve)
        sys.exit(1)  # Exit if configuration validation fails

# Function to validate the loaded configuration
def validate_config(config):
    required_keys = ['connections', 'port', 'ip_whitelist', 'log_enabled', 'logrotate_days', 'log_filename', 'secret_key']
    for key in required_keys:
        if key not in config:
            raise ValueError(f"Missing '{key}' key in the configuration file.")
    for key, value in config['connections'].items():
        if not all(k in value for k in ('type', 'host', 'port', 'username', 'password', 'database')):
            raise ValueError(f"Missing one or more required data under '{key}' key.")

# Loading configuration file and validating
config_path = 'config.json'
config = load_config(config_path)

# Setting up logging with rotation if logging is enabled
if config.get('log_enabled'):
    log_filename = config['log_filename']
    logrotate_days = config['logrotate_days']
    logging.basicConfig(level=logging.DEBUG,
                        format='%(asctime)s - %(levelname)s - %(message)s',
                        handlers=[
                            TimedRotatingFileHandler(log_filename, when='D', interval=1, backupCount=logrotate_days),
                            logging.StreamHandler(sys.stdout)
                        ])
else:
    logging.disable(logging.CRITICAL)

logging.info(f"Successfully loaded '{config_path}' configuration file.")

# Secret key for JWT
secret_key = config['secret_key']

# Function to establish database connection
def get_db_connection(conn):
    db_config = config['connections'].get(conn)
    if not db_config:
        return None

    db_type = db_config['type']
    username = db_config['username']
    password = db_config['password']
    host = db_config['host']
    port = db_config['port']
    database = db_config['database']

    if db_type == 'mysql':
        engine = create_engine(f"mysql+pymysql://{username}:{password}@{host}:{port}/{database}")
    elif db_type == 'pgsql':
        engine = create_engine(f"postgresql+psycopg2://{username}:{password}@{host}:{port}/{database}")
    elif db_type == 'dblib':
        connection_string = f"DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={host},{port};DATABASE={database};UID={username};PWD={password}"
        engine = create_engine(f"mssql+pyodbc:///?odbc_connect={connection_string}")
    else:
        return None

    return engine

# Endpoint for executing SQL queries
@app.route('/execute', methods=['GET'])
def execute_query():
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        logging.error("Authorization header missing or invalid.")
        return jsonify({'status': 0, 'response': 'Authorization header missing or invalid'}), 401

    token = auth_header.split(' ')[1]
    try:
        decoded_token = jwt.decode(token, secret_key, algorithms=['HS256'])
        conn = decoded_token.get('conn')
        sql = decoded_token.get('sql')
        logging.info(f"SQL: '{sql}'")
    except jwt.ExpiredSignatureError:
        logging.error("Token expired.")
        return jsonify({'status': 0, 'response': 'Token expired'}), 401
    except jwt.InvalidTokenError as e:
        logging.error(f"Invalid token: {e}")
        return jsonify({'status': 0, 'response': str(e)}), 401

    if not conn or not sql:
        logging.error("Connection and SQL query are required.")
        return jsonify({'status': 0, 'response': 'Connection and SQL query are required'}), 400

    client_ip = request.remote_addr
    ip_whitelist = config['ip_whitelist']
    if client_ip not in ip_whitelist:
        logging.error(f"Unauthorized IP address: {client_ip}")
        return jsonify({'status': 0, 'response': 'Unauthorized IP address'}), 403

    engine = get_db_connection(conn)
    if not engine:
        logging.error(f"Invalid connection or database configuration not found for '{conn}'.")
        return jsonify({'status': 0, 'response': 'Invalid connection or database configuration not found'}), 404

    connection = engine.connect()
    transaction = connection.begin()
    try:
        result = connection.execute(text(sql))
        if result.returns_rows:
            rows = [dict(row._mapping) for row in result.fetchall()]
            transaction.commit()
            return jsonify({'status': 1, 'response': rows})
        else:
            response_code = 1 if result.rowcount > 0 else 0
            logging.info(f"Rows affected: {result.rowcount}")
            transaction.commit()
            return jsonify({'status': 1, 'response': response_code})
    except SQLAlchemyError as e:
        logging.error(f"Error while executing SQL query: {e}")
        transaction.rollback()
        return jsonify({'status': 0, 'response': str(e)}), 500
    finally:
        connection.close()

# Running the Flask app
if __name__ == '__main__':
    port = config.get('port')
    app.run(host='0.0.0.0', port=port, debug=False)
