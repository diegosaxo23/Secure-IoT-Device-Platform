# Runtime Logs Directory

Runtime logs are created locally and are not part of the public repository.

The host Manufacturing Agent writes its PID/log files here, and `broker/` is reserved for Mosquitto log output. Sensitive values such as bootstrap secrets and private keys must not be logged.

All generated log files are ignored by Git and excluded from the Docker build context.
