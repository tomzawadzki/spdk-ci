FROM python:3.13-alpine

RUN pip install \
    requests \
    pygerrit2 \
    prettytable
COPY mergable_changes.py /app/mergable_changes.py

CMD [ "python", "/app/mergable_changes.py"]
