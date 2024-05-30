# Kvery Agent

Kvery Agent is a Python-based application designed to manage database connections and logging configurations. This application can be easily set up and customized through its configuration file.

## Features

- Supports MySQL, PostgreSQL, and MSSQL database connections.
- Configurable IP whitelisting for added security.
- Logging capabilities with log rotation.

## Prerequisites

- Python 3.x
- MySQL
- PostgreSQL
- ODBC Driver 17 for SQL Server (for MSSQL support)

## Installation

1. Clone the repository:

    ```bash
    git clone https://github.com/KoltSystems/kvery-agent.git
    cd kvery-agent
    ```

2. Install the required Python packages:

    ```bash
    pip install Flask SQLAlchemy PyMySQL psycopg2-binary pyodbc PyJWT
    ```

3. Install the ODBC Driver 17 for SQL Server:

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

The application configuration is managed through the `config.json` file.

## Usage

1. Ensure your database server is running and the configuration file is properly set up.
2. Run the application:

    ```bash
    python3 kvery-agent.py
    ```

## Running as a Service

To run Kvery Agent as a service on a Unix-based system, you can create a systemd service file.

1. Create a systemd service file:

    ```bash
    sudo nano /etc/systemd/system/kvery-agent.service
    ```

2. Add the following content to the file:

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

    Make sure to replace `/path/to/kvery-agent.py` and `/path/to/kvery-agent` with the actual paths to your script and working directory. Also, replace `your-username` with the user you want to run the service as.

3. Reload systemd to apply the new service:

    ```bash
    sudo systemctl daemon-reload
    ```

4. Start the service:

    ```bash
    sudo systemctl start kvery-agent
    ```

5. Enable the service to start on boot:

    ```bash
    sudo systemctl enable kvery-agent
    ```

## Logging

The application generates logs that help in monitoring its activities. Logs are stored in the file specified in the configuration file and are rotated based on the specified number of days.

## Security

Ensure to update the `ip_whitelist` with the IP addresses that are allowed to access the application to enhance security. The `secret_key` should be set to a strong, unique value.

## Contributing

Contributions are welcome! Please submit a pull request or open an issue for any improvements or bugs.

## License

This project is licensed under the MIT License.
