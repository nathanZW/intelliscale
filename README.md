# Intelliscale
### The iterative branch full of the latest improvements
## Get started
### Linux
Run the following script to get started 
[intelliscale_setup.sh](https://github.com/user-attachments/files/24448348/intelliscale_setup.sh)

Should you need to remove the program use the following 
[cleanup_intelliscale.sh](https://github.com/user-attachments/files/24448387/cleanup_intelliscale.sh)

This will configure Intelliscale to run on boot.

To access intelliscale using a Host name some configuration is needed
1. Access the hosts file from the terminal
```
sudo nano /etc/hosts
```
2. where you see your IP add an entry specifying a new host name like so
```
0.0.0.0 localhost
0.0.0.0 youcomputername
0.0.0.0 intelliscale.local <--- your new entry
```
3. Configure Nginx to serve on the address
```
sudo nano /etc/nginx/sites-available/intelliscale
```
```
server_name intelliscale.local 0.0.0.0; <--- replace intelliscale.local with your new host name
```
4. Restart Nginx and systemd configurations
```
sudo systemctl restart nginx
sudo systemctl daemon-reload
```

### Windows
Intelliscale offers limited functionality on Windows

Clone the repository and run the .bat file for basic functionality

## Get Set up
Check in with Support, there may already be a configuration file available for you

Configuration files can imported and exported freely by an Admin. The option is available from the Admin Dashboard
