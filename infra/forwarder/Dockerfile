FROM python:3.14-alpine

RUN pip install requests \
	jinja2 \
	pygerrit2
COPY forwarder/forwarder.py /app/forwarder.py
COPY forwarder/queue_status_template.html /app/queue_status_template.html
COPY templates/ /app/templates/

WORKDIR /app
CMD [ "python", "forwarder.py"]
