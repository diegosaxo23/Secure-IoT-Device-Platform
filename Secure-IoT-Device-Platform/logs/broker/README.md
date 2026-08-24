# Broker Logs Directory

This directory is the persistent mount point for Mosquitto logs. Generated log files are excluded from version control.

Broker logs can be used to verify TLS connections, ACL denials, reloads, and operational errors. They must not be treated as a source of bootstrap secrets or private keys.
