FROM python:3.13-alpine

RUN pip install \
    requests \
    pygerrit2 \
    prettytable \
    jinja2
COPY mergable_changes.py /app/mergable_changes.py
COPY template.html /app/template.html

WORKDIR /app
CMD [ "python", "mergable_changes.py"]
