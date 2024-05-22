# Kvery Agent

Kvery Agent is a Python-based application designed to manage database connections and logging configurations. This application can be easily set up and customized through its configuration file.

## Features

- Supports MySQL, PostgreSQL, and MSSQL database connections.
- Configurable IP whitelisting for added security.
- Logging capabilities with log rotation.

## Prerequisites

- Python 3.x
- MySQL, PostgreSQL, or MSSQL server

## Installation

1. Clone the repository:

    ```bash
    git clone git@github.com:KoltSystems/kvery-agent.git
    cd kvery-agent
    ```

2. Install the required Python packages:

    ```bash
    pip install sqlalchemy pymysql psycopg2 pyodbc
    ```

## Configuration

The application configuration is managed through the `config.json` file. Below is an example configuration:

```json
{
    "connections": {
        "db1": {
            "type": "mysql",
            "host": "127.0.0.1",
            "port": 3306,
            "username": "root",
            "password": "",
            "database": ""
        },
        "db2": {
            "type": "pgsql",
            "host": "127.0.0.1",
            "port": 5432,
            "username": "postgres",
            "password": "",
            "database": ""
        },
        "db3": {
            "type": "dblib",
            "host": "127.0.0.1",
            "port": 1433,
            "username": "sa",
            "password": "",
            "database": ""
        }
    },
    "port": 1337,
    "ip_whitelist": ["164.92.167.149"],
    "log_enabled": true,
    "logrotate_days": 7,
    "log_filename": "agent.log",
    "secret_key": "secret_key"
}
