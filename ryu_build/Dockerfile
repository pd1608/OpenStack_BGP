FROM python:3.8-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libffi-dev libssl-dev iproute2 iputils-ping \
    && rm -rf /var/lib/apt/lists/*

# Ryu 4.34 requires older setuptools + eventlet; install in this order
RUN pip install --no-cache-dir setuptools==67.6.0
RUN pip install --no-cache-dir eventlet==0.30.2
RUN pip install --no-cache-dir ryu==4.34

# Copy app and config
WORKDIR /ryu-app
COPY ryu_bgp_app.py  /ryu-app/ryu_bgp_app.py
COPY ryu-bgp.conf    /ryu-app/ryu-bgp.conf

# OpenFlow port
EXPOSE 6633
# BGP port
EXPOSE 179

CMD ["ryu-manager", "--config-file", "/ryu-app/ryu-bgp.conf", "/ryu-app/ryu_bgp_app.py"]