# API Routers

- `provisioning.py` implements the device challenge/HMAC and CSR enrollment endpoints.
- `admin.py` implements authenticated device administration, runtime state, command publication, bootstrap reset, and certificate revocation.
- `dashboard.py` renders Fleet, device details, manual registration, and the password-protected **Reset Project Data** form.
- `simulation.py` renders Simulation Lab and proxies status, explicit enable/disable, fleet start, and stop operations to the internal Simulation Manager.
- `manufacturing.py` renders Manufacturing, reports host/serial-port status, starts allowlisted programming jobs, and proxies live job progress from the host agent.

Administrative routes use the dashboard HTTP Basic credentials. Browser write operations use a session CSRF token. The project reset additionally requires the operator to re-enter the current dashboard password.
