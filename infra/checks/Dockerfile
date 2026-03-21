FROM python:3.13-alpine

RUN pip install --no-cache-dir fastapi \
	uvicorn \
	requests \
	pygerrit2
COPY *.py /app/
WORKDIR /app
RUN mkdir -p /app/data
EXPOSE 8001
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8001"]
