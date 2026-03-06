FROM python:3.13-alpine

RUN pip install requests \
	jinja2 \
	pygerrit2
COPY forwarder.py /app/forwarder.py
COPY queue_status_template.html /app/queue_status_template.html

WORKDIR /app
CMD [ "python", "forwarder.py"]
