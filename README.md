# Kvery Agent

Kvery Agent is a small HTTP service that runs next to a database which Kvery cannot reach directly. Kvery sends each
SQL statement to the agent in a signed request; the agent runs it on the configured database and answers with the
result as JSON.

## Features

- MySQL / MariaDB, PostgreSQL and SQL Server connections, any number of them in one agent.
- Every request is signed with a shared secret (JSON Web Token, HS256). A token that carries an expiry time is refused
  after it, and a token that also carries a one-time ID is accepted once only, so a request that was read on the
  network cannot be run again.
- A statement travels in the request header, or, when it is long, in the request body.
- IP whitelist: only the listed addresses are served; any other address is refused on every path.
- Log file with daily rotation. Statements and result rows are not written to the log.

## Requirements

- Python 3.7 or newer
- The Python packages of `requirements.txt`
- The driver package of each database type in use:

  | Database | `type` in `config.json` | Package |
  |---|---|---|
  | MySQL / MariaDB | `mysql` | `PyMySQL` |
  | PostgreSQL | `pgsql` | `psycopg2-binary` |
  | SQL Server | `dblib` | `pyodbc` and the Microsoft ODBC driver |

## Installation

1. Clone the repository:

    ```bash
    git clone https://github.com/KoltSystems/kvery-agent.git
    cd kvery-agent
    ```

2. Install the Python packages and the driver of your database:

    ```bash
    pip install -r requirements.txt
    pip install PyMySQL            # MySQL / MariaDB
    pip install psycopg2-binary    # PostgreSQL
    pip install pyodbc             # SQL Server
    ```

3. SQL Server only: install the Microsoft ODBC driver.

    - **Ubuntu/Debian**:
      ```bash
      sudo su
      curl https://packages.microsoft.com/keys/microsoft.asc | apt-key add -
      curl https://packages.microsoft.com/config/ubuntu/$(lsb_release -rs)/prod.list > /etc/apt/sources.list.d/mssql-release.list
      sudo apt-get update
      sudo ACCEPT_EULA=Y apt-get install -y msodbcsql17
      sudo apt-get install -y unixodbc-dev
      ```

    - **CentOS/RHEL**:
      ```bash
      sudo su
      curl https://packages.microsoft.com/keys/microsoft.asc | sudo tee /etc/pki/rpm-gpg/Microsoft.asc
      curl https://packages.microsoft.com/config/rhel/$(rpm -E %{rhel})/prod.repo | sudo tee /etc/yum.repos.d/msprod.repo
      sudo yum remove unixODBC # to avoid conflicts
      sudo ACCEPT_EULA=Y yum install -y msodbcsql17
      sudo yum install -y unixODBC-devel
      ```

## Configuration

The agent reads `config.json` from its working directory, or the file given as its first command-line argument. It
does not start when a required key is missing or a value cannot be used; the reason is printed.

| Key | Required | Meaning |
|---|---|---|
| `connections` | yes | The databases the agent may use, by name. Kvery refers to a connection by this name. |
| `port` | yes | TCP port the agent listens on. |
| `ip_whitelist` | yes | IP addresses that are served. Every other address gets HTTP 403 on every path. List the address of the Kvery server here. |
| `secret_key` | yes | Shared secret that signs the requests: at least 16 characters (see `min_secret_length`), 32 or more random characters are recommended. Enter the same value at the connection in Kvery. |
| `log_enabled` | yes | `true` writes the log file and the console log, `false` logs nothing. |
| `log_filename` | yes | Name of the log file. |
| `logrotate_days` | yes | Number of daily log files that are kept. |
| `host` | no | Address the agent listens on. Default: `0.0.0.0` (every interface). |
| `date_format` | no | How date and date-time values are returned: `iso` gives ISO 8601 text with a space between the date and the time (`2025-01-02 00:00:00`), `http` gives HTTP date text (`Thu, 02 Jan 2025 00:00:00 GMT`). Default without the key: `http`. |
| `min_secret_length` | no | Smallest length of `secret_key` the agent starts with. It can be raised, but not set below 16. Default: `16`. |
| `algorithms` | no | Signing algorithms a token may use: a list of one or more of `HS256`, `HS384` and `HS512`. The algorithm selected at the connection in Kvery has to be in the list. Default: `["HS256"]`. |
| `require_expiry` | no | `true` refuses a token without an expiry time or without a one-time ID, so every request can be run once only. Kvery sends both. Default without the key: `false`. |
| `expiry_leeway` | no | Seconds a token is still accepted after its expiry time. Raise it when the clock of the agent's machine cannot be kept in step with the caller's clock. Default: `0`. |
| `threads` | no | Number of statements that run at the same time with the `waitress` server; further requests wait for a free thread. Default: `100`. |
| `connection_limit` | no | Most sockets the `waitress` server keeps open at the same time; its listening socket and its internal socket count too, so two fewer callers can be connected. Default: `100`. |
| `max_header_bytes` | no | Largest request header the `waitress` server takes, in bytes. It limits a statement that is sent in the header. Default: `262144` (256 KB). |
| `max_body_bytes` | no | Largest request body the agent reads, in bytes. A statement that is sent in the body can be about three quarters of it. Default: `16777216` (16 MB). |
| `log_level` | no | Lowest level that is written to the log: `DEBUG`, `INFO`, `WARNING` or `ERROR`. Default: `INFO`. |
| `trusted_proxies` | no | Addresses of reverse proxies in front of the agent. For a request that arrives from one of them, the caller's address is read from the `X-Forwarded-For` header. Default: none. |
| `log_sql` | no | `true` writes the text of every statement to the log. Statements can hold personal data and passwords: switch it on only for troubleshooting. Default: `false`. |

Each entry of `connections` has these keys:

| Key | Meaning |
|---|---|
| `type` | `mysql`, `pgsql` or `dblib` (SQL Server). |
| `host`, `port` | Address and port of the database server. |
| `username`, `password` | Database account (see the notes below for characters with a special meaning). |
| `database` | Name of the database. Driver options may follow it (see the notes below). |
| `odbc_driver` | SQL Server only, optional: name of the installed ODBC driver. Default: `ODBC Driver 17 for SQL Server`. |

MySQL / MariaDB and PostgreSQL: the values of a connection are read as parts of a URL.

- In the user name and the password, a percent sign followed by two hexadecimal digits is decoded, so write a
  percent sign as `%25`. Write a colon or a slash of the user name as `%3A` or `%2F`. Every other character, also an
  `@` in the password, is written as it is.
- Driver options may follow the database name after a question mark, for example
  `"database": "reports?charset=utf8mb4"` (MySQL / MariaDB) or `"database": "reports?sslmode=require"` (PostgreSQL).

SQL Server: the values of a connection are joined into an ODBC connection string.

- Options may follow the database name after a semicolon, for example
  `"database": "reports;TrustServerCertificate=yes"`.
- Apart from that, a value cannot contain `;`, `{`, `}`, `+`, `&` or `%`.

Example with one MySQL connection:

```json
{
    "connections": {
        "reports": {
            "type": "mysql",
            "host": "127.0.0.1",
            "port": 3306,
            "username": "report_user",
            "password": "...",
            "database": "reports"
        }
    },
    "port": 1337,
    "ip_whitelist": ["203.0.113.10"],
    "log_enabled": true,
    "logrotate_days": 7,
    "log_filename": "agent.log",
    "secret_key": "<32 or more random characters>",
    "date_format": "iso",
    "require_expiry": true
}
```

A random secret can be generated with:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

## Security

- **Secret:** whoever knows `secret_key` can sign requests. Use a long random value, keep `config.json` readable only
  by the account that runs the agent (`chmod 600 config.json`), and use a different secret for every agent.
- **One-time requests:** with `require_expiry` the agent runs every request once only, and only until its expiry
  time. The one-time IDs are kept in memory until their tokens expire. The expiry time is compared with the clock of
  the agent's machine, so keep that clock synchronised: a request is refused as expired when the clock is ahead by
  more than the lifetime the caller gives its tokens. `expiry_leeway` allows for a clock that cannot be kept in step.
- **IP whitelist:** list only the addresses that have to reach the agent, and let the firewall accept the agent's port
  only from them.
- **Database account:** give the account of each connection only the rights the queries need.
- **Encryption:** the agent itself speaks plain HTTP, which protects neither the statements nor the results on the
  network. Put a reverse proxy with TLS in front of it and enter an `https://` address in Kvery. With the proxy on
  the same machine, let the agent listen on the loopback interface only and name the proxy in `trusted_proxies`, so
  the IP whitelist sees the real caller:

    ```json
    "host": "127.0.0.1",
    "trusted_proxies": ["127.0.0.1"]
    ```

    ```nginx
    server {
        listen 443 ssl;
        server_name agent.example.com;
        ssl_certificate     /etc/ssl/agent.example.com/fullchain.pem;
        ssl_certificate_key /etc/ssl/agent.example.com/privkey.pem;
        large_client_header_buffers 4 256k;
        client_max_body_size 16m;

        location / {
            proxy_pass http://127.0.0.1:1337;
            proxy_set_header X-Forwarded-For $remote_addr;
            proxy_read_timeout 300s;
        }
    }
    ```

    The read timeout of the proxy has to be longer than the longest statement runs, and its body size limit at least
    `max_body_bytes`. Kvery verifies the certificate of an `https://` address, so the proxy needs a certificate that
    a public certificate authority issued for its host name.

## Limits

- A request carries its token in the Authorization header of a GET request, or as the body of a POST request. In the
  header, the header size limit of the server (`max_header_bytes` with `waitress`, and the limit of a proxy in front
  of the agent) is the longest statement that gets through; the development server of Flask accepts shorter headers
  than `waitress` does. In the body, the limit is `max_body_bytes` (and the body size limit of a proxy in front of
  the agent).
- With the `waitress` server, at most `threads` statements run at the same time; further requests wait for a free
  thread. The server keeps at most `connection_limit` sockets open, and further callers wait to be connected.

## Usage

1. Make sure the database server is running and `config.json` is filled in.
2. Start the agent from its directory:

    ```bash
    python3 kvery-agent.py
    ```

    To use a configuration file at another place, give its path as argument:

    ```bash
    python3 kvery-agent.py /etc/kvery-agent/config.json
    ```

The agent serves requests with the `waitress` server. Without that package it falls back to the development server of
Flask, which is meant for testing only; the log says so at start.

## Running as a Service

To run the agent as a service on a Linux system, create a systemd unit.

1. Create the unit file:

    ```bash
    sudo nano /etc/systemd/system/kvery-agent.service
    ```

2. Add the following content:

    ```ini
    [Unit]
    Description=Kvery Agent Service
    After=network.target

    [Service]
    ExecStart=/usr/bin/python3 /path/to/kvery-agent.py
    WorkingDirectory=/path/to/kvery-agent
    StandardOutput=inherit
    StandardError=inherit
    Restart=always
    User=your-username
    Environment=PYTHONUNBUFFERED=1

    [Install]
    WantedBy=multi-user.target
    ```

    Replace `/path/to/kvery-agent.py` and `/path/to/kvery-agent` with the paths of the script and its directory, and
    `your-username` with the account that runs the service.

3. Reload systemd, start the service and enable it at boot:

    ```bash
    sudo systemctl daemon-reload
    sudo systemctl start kvery-agent
    sudo systemctl enable kvery-agent
    ```

## Manage the Service

```bash
sudo systemctl start kvery-agent
sudo systemctl stop kvery-agent
sudo systemctl restart kvery-agent
```

Restart the service after every change of `config.json`.

## Logging

With `log_enabled`, the agent writes its log to the console and to the file named by `log_filename`; a new file is
started every day and `logrotate_days` files are kept. The log holds the start of the agent, refused requests, the
length of each statement with the name of its connection, the number of affected rows and database errors: a failed
statement with the kind and the code of the error, a failed connection with the message of the database driver (the
development server of Flask also logs a line for every request). `log_level` sets the lowest level that is written:
the lines about served requests are `INFO` lines, refusals and failures are `ERROR` lines. The text of the
statements is logged only with `log_sql` (as `INFO` lines, and in the line of a failed statement).

## Updating

`config.json` in the repository is a template. Keep a copy of your own file before you update the agent, and restore
it afterwards. Then install the packages of `requirements.txt` again and restart the agent:

```bash
pip install -r requirements.txt
sudo systemctl restart kvery-agent
```

## Contributing

Contributions are welcome! Please submit a pull request or open an issue for any improvements or bugs.

## License

This project is licensed under the MIT License.
