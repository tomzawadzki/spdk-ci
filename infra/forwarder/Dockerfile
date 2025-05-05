FROM python:3.13-alpine

RUN pip install requests
COPY forwarder.py /app/forwarder.py

CMD [ "python", "/app/forwarder.py"]
