# Intelliscale
> **The iterative fork with the latest improvements and features.**

## 🚀 Getting Started

### Linux (Recommended)
This installation will configure Intelliscale to run automatically on boot.

1. **Installation:** Download and run the setup script:
   * [intelliscale_setup.sh](https://github.com/user-attachments/files/24711691/intelliscale_setup.sh)
2. **Uninstallation:** If you need to remove the program and its configurations:
   * [cleanup_intelliscale.sh](https://github.com/user-attachments/files/24448387/cleanup_intelliscale.sh)

---

### 🌐 Custom Hostname Configuration
To access Intelliscale via a custom hostname (e.g., `http://intelliscale.local`), follow these steps:

#### 1. Map the Local IP
Open your hosts file:
`sudo nano /etc/hosts`

Add your entry at the bottom:
`127.0.0.1   localhost`
`127.0.0.1   yourcomputername`
`127.0.0.1   intelliscale.local  # <--- Your new entry`

#### 2. Configure Nginx
Edit the Nginx site configuration:
`sudo nano /etc/nginx/sites-available/intelliscale`

Update the `server_name` line:
`server_name intelliscale.local; # <--- Replace with your chosen hostname`

#### 3. Apply Changes
Restart the services to finalize the setup:
`sudo systemctl daemon-reload`
`sudo systemctl restart nginx`

---

### 🪟 Windows
*Note: Intelliscale offers limited functionality on Windows environments.*

1. Clone the repository to your local machine.
2. Locate and run the `.bat` file for basic functionality.

---

## ⚙️ Configuration & Support

### Pre-built Configs
Before setting everything up manually, **check in with Support**. There may already be a specific configuration file optimized for your use case.

### Importing/Exporting
Admins can freely manage environment settings:
* Navigate to the **Admin Dashboard**.
* Use the **Import/Export** tools to move configuration files between instances.
