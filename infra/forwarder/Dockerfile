FROM python:3.13-alpine

RUN pip install requests \
	jinja2 \
	pygerrit2
COPY common/ /app/common/
COPY forwarder/forwarder.py /app/forwarder.py
COPY forwarder/queue_status_template.html /app/queue_status_template.html

WORKDIR /app
CMD [ "python", "forwarder.py"]
