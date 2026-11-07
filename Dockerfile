FROM node:24-alpine AS web
WORKDIR /web
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.lock.txt ./requirements.lock.txt
RUN pip install --no-cache-dir -r requirements.lock.txt
COPY backend ./backend
COPY --from=web /web/dist ./web
RUN useradd --create-home sentiment && mkdir -p /app/backend/data && chown -R sentiment:sentiment /app
USER sentiment
EXPOSE 8000
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
