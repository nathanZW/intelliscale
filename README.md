# Intelliscale

## Getting Started

### Linux
This installation will configure Intelliscale to run automatically on boot. Services include celery, nginx, gunicorn, and intelliscale-satellite

1. **Installation:** Download and run the setup script:
   * [intelliscale_setup.sh](https://github.com/user-attachments/files/25759674/intelliscale_setup.sh)

2. **Uninstallation:** If you need to remove the program and its configurations:
   * [intelliscale_cleanup.sh](https://github.com/user-attachments/files/25759684/intelliscale_cleanup.sh)

---

### 🌐 Custom Hostname Configuration
To access Intelliscale via a custom hostname (e.g., `http://intelliscale.local`), follow these steps:

#### 1. Map the Local IP
Open your hosts file:
`sudo nano /etc/hosts`

Add your entry at the bottom:
`127.0.0.1   localhost`
`127.0.1.1   yourcomputername`
`127.0.0.1   intelliscale.local  # <--- Your new entry`

#### 2. Configure Nginx
Edit the Nginx site configuration:
`sudo nano /etc/nginx/sites-enabled/intelliscale`

Update the `server_name` line:
`server_name intelliscale.local; # <--- add your chosen hostname`

#### 3. Apply Changes
Restart the services to finalize the setup:
`sudo systemctl daemon-reload`
`sudo systemctl restart nginx`

---

## ⚙️ Configuration & Support

### Pre-built Configs
Before setting everything up manually, **check in with Support**. There may already be a specific configuration file optimized for your use case.

### Importing/Exporting
Admins can freely manage environment settings:
* Navigate to the **Admin Dashboard**.
* Use the **Import/Export** tools to move configuration files between instances.
