FROM python:3.14-alpine

RUN pip install \
    requests \
    pygerrit2 \
    prettytable \
    jinja2
COPY mergable_changes/mergable_changes.py /app/mergable_changes.py
COPY mergable_changes/template.html /app/template.html
COPY templates/ /app/templates/

WORKDIR /app
CMD [ "python", "mergable_changes.py"]
