#!/usr/bin/env python3
"""
AI Generation Service for Railway
Provides REST API endpoints for Imagen and Veo generation
"""

from flask import Flask, request, jsonify, send_file
import requests
import json
import os
import time
import base64
import tempfile
import threading
from datetime import datetime

app = Flask(__name__)

# Конфигурация моделей (из вашего universal_ai_generator.py)
MODELS = {
    # === VEO МОДЕЛИ ===
    "veo3": {
        "model_id": "veo-3.0-generate-001",
        "type": "video",
        "endpoint": "predictLongRunning",
        "description": "Veo 3 (GA)",
        "supported_ratios": ["16:9", "9:16"],
        "default_ratio": "16:9",
        "duration": 8,
        "audio": True
    },
    "veo3-preview": {
        "model_id": "veo-3.0-generate-preview", 
        "type": "video",
        "endpoint": "predictLongRunning",
        "description": "Veo 3 Preview - новые функции + аудио",
        "supported_ratios": ["16:9"],
        "default_ratio": "16:9",
        "duration": 8,
        "audio": True
    },
    "veo2": {
        "model_id": "veo-2.0-generate-001",
        "type": "video", 
        "endpoint": "predictLongRunning",
        "description": "Veo 2 (GA)",
        "supported_ratios": ["16:9", "9:16"],
        "default_ratio": "16:9",
        "duration": 8,
        "audio": False
    },
    
    # === IMAGEN МОДЕЛИ ===
    "imagen4": {
        "model_id": "imagen-4.0-generate",
        "type": "image",
        "endpoint": "predict",
        "description": "Imagen 4 (новейшая) - высококачественные изображения",
        "supported_ratios": ["1:1", "3:4", "4:3", "9:16", "16:9"],
        "default_ratio": "1:1"
    },
    "imagen4-fast": {
        "model_id": "imagen-4.0-fast-generate", 
        "type": "image",
        "endpoint": "predict",
        "description": "Imagen 4 Fast - быстрая генерация изображений",
        "supported_ratios": ["1:1", "3:4", "4:3", "9:16", "16:9"],
        "default_ratio": "1:1"
    },
    "imagen3": {
        "model_id": "imagen-3.0-generate-001",
        "type": "image",
        "endpoint": "predict", 
        "description": "Imagen 3 (GA) - стабильная генерация изображений",
        "supported_ratios": ["1:1", "3:4", "4:3", "9:16", "16:9"],
        "default_ratio": "1:1"
    }
}

PROJECT_ID = os.getenv("GOOGLE_PROJECT_ID", "sodium-gateway-465110-q3")
LOCATION = os.getenv("GOOGLE_LOCATION", "us-central1")

# Хранилище для долгосрочных операций
operations_store = {}

def get_access_token():
    """Получает access token из переменных окружения или cred.json"""
    
    # Попробуем получить из переменных окружения
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET") 
    refresh_token = os.getenv("GOOGLE_REFRESH_TOKEN")
    
    if not all([client_id, client_secret, refresh_token]):
        # Fallback на cred.json
        try:
            with open("cred.json", "r") as f:
                creds = json.load(f)
                client_id = creds["client_id"]
                client_secret = creds["client_secret"]
                refresh_token = creds["refresh_token"]
        except Exception as e:
            return None
    
    token_url = "https://oauth2.googleapis.com/token"
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token"
    }
    
    try:
        response = requests.post(token_url, data=data)
        if response.status_code == 200:
            return response.json()["access_token"]
        else:
            return None
    except Exception as e:
        return None

@app.route("/", methods=["GET"])
def home():
    """Главная страница с информацией о сервисе"""
    return jsonify({
        "service": "AI Generation Service",
        "version": "1.0.0",
        "models": list(MODELS.keys()),
        "endpoints": {
            "generate": "/generate",
            "status": "/status/<operation_id>",
            "models": "/models",
            "health": "/health"
        }
    })

@app.route("/models", methods=["GET"])
def get_models():
    """Возвращает список доступных моделей"""
    return jsonify({
        "models": MODELS,
        "count": len(MODELS)
    })

@app.route("/health", methods=["GET"])
def health_check():
    """Проверка здоровья сервиса"""
    token = get_access_token()
    return jsonify({
        "status": "healthy" if token else "unhealthy",
        "timestamp": datetime.now().isoformat(),
        "google_auth": "ok" if token else "failed"
    })

@app.route("/generate", methods=["POST"])
def generate():
    """Основной endpoint для генерации контента"""
    
    try:
        data = request.get_json()
        
        # Валидация входных данных
        if not data:
            return jsonify({"error": "Отсутствуют данные в запросе"}), 400
            
        model_name = data.get("model")
        prompt = data.get("prompt")
        
        if not model_name or not prompt:
            return jsonify({"error": "Обязательные поля: model, prompt"}), 400
            
        if model_name not in MODELS:
            return jsonify({
                "error": f"Неизвестная модель: {model_name}",
                "available_models": list(MODELS.keys())
            }), 400
        
        model_config = MODELS[model_name]
        
        # Параметры генерации
        aspect_ratio = data.get("aspectRatio") or data.get("aspect_ratio") or model_config["default_ratio"]
        resolution = data.get("resolution", "720p")
        
        # Проверка поддерживаемого аспект ратио
        if aspect_ratio not in model_config["supported_ratios"]:
            return jsonify({
                "error": f"Неподдерживаемый аспект ратио {aspect_ratio}",
                "supported": model_config["supported_ratios"]
            }), 400
        
        # Генерируем контент
        result = generate_content(model_name, prompt, aspect_ratio, resolution)
        
        if result is None:
            return jsonify({"error": "Ошибка генерации контента"}), 500
            
        return jsonify(result)
        
    except Exception as e:
        return jsonify({"error": f"Внутренняя ошибка: {str(e)}"}), 500

def generate_content(model_name, prompt, aspect_ratio, resolution):
    """Генерирует контент используя Google Vertex AI"""
    
    model_config = MODELS[model_name]
    
    # Получаем токен
    access_token = get_access_token()
    if not access_token:
        return {"error": "Не удалось получить токен авторизации"}
    
    # Формируем URL
    url = f"https://{LOCATION}-aiplatform.googleapis.com/v1/projects/{PROJECT_ID}/locations/{LOCATION}/publishers/google/models/{model_config['model_id']}:{model_config['endpoint']}"
    
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    
    # Формируем данные запроса
    if model_config["type"] == "video":
        # Конфигурация для видео
        data = {
            "instances": [{
                "prompt": prompt
            }],
            "parameters": {
                "sampleCount": 1,
                "durationSeconds": model_config["duration"],
                "aspectRatio": aspect_ratio
            }
        }
        
        # Добавляем аудио для Veo 3
        if model_config.get("audio"):
            data["parameters"]["generateAudio"] = True
            
        # Добавляем разрешение для Veo 3
        if "veo-3" in model_config["model_id"] and resolution:
            data["parameters"]["resolution"] = resolution
            
    else:
        # Конфигурация для изображений
        data = {
            "instances": [{
                "prompt": prompt
            }],
            "parameters": {
                "sampleCount": 1,
                "aspectRatio": aspect_ratio
            }
        }
    
    try:
        response = requests.post(url, headers=headers, json=data)
        
        if response.status_code == 200:
            result = response.json()
            
            if model_config["type"] == "video":
                # Для видео - долгосрочная операция
                operation_name = result.get("name")
                operation_id = f"op_{int(time.time())}_{hash(operation_name) % 10000}"
                
                # Сохраняем операцию
                operations_store[operation_id] = {
                    "operation_name": operation_name,
                    "model_id": model_config["model_id"],
                    "access_token": access_token,
                    "status": "processing",
                    "created_at": datetime.now().isoformat(),
                    "model_name": model_name,
                    "prompt": prompt
                }
                
                # Запускаем асинхронную обработку
                threading.Thread(
                    target=poll_video_operation_async,
                    args=(operation_id, operation_name, model_config["model_id"], access_token)
                ).start()
                
                return {
                    "operation_id": operation_id,
                    "status": "processing",
                    "message": "Генерация видео запущена",
                    "estimated_time": "2-5 минут",
                    "status_url": f"/status/{operation_id}"
                }
            else:
                # Для изображений - сразу результат
                return handle_image_result(result, model_name, prompt)
        else:
            return {
                "error": f"Ошибка API: {response.status_code}",
                "details": response.text
            }
            
    except Exception as e:
        return {"error": f"Ошибка запроса: {str(e)}"}

def poll_video_operation_async(operation_id, operation_name, model_id, access_token):
    """Асинхронно проверяет статус операции генерации видео"""
    
    url = f"https://{LOCATION}-aiplatform.googleapis.com/v1/projects/{PROJECT_ID}/locations/{LOCATION}/publishers/google/models/{model_id}:fetchPredictOperation"
    
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }
    
    data = {"operationName": operation_name}
    
    max_attempts = 30
    for attempt in range(max_attempts):
        try:
            response = requests.post(url, headers=headers, json=data)
            result = response.json()
            
            if result.get("done"):
                # Операция завершена
                operations_store[operation_id]["status"] = "completed"
                operations_store[operation_id]["result"] = result
                operations_store[operation_id]["completed_at"] = datetime.now().isoformat()
                
                # Обрабатываем результат
                if result.get("response", {}).get("videos"):
                    video = result["response"]["videos"][0]
                    if "bytesBase64Encoded" in video:
                        # Сохраняем видео во временный файл
                        video_data = base64.b64decode(video["bytesBase64Encoded"])
                        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".mp4")
                        temp_file.write(video_data)
                        temp_file.close()
                        
                        operations_store[operation_id]["file_path"] = temp_file.name
                        operations_store[operation_id]["file_type"] = "video/mp4"
                
                break
                
            time.sleep(15)  # Ждем 15 секунд между проверками
            
        except Exception as e:
            operations_store[operation_id]["status"] = "error"
            operations_store[operation_id]["error"] = str(e)
            break
    
    if operations_store[operation_id]["status"] == "processing":
        operations_store[operation_id]["status"] = "timeout"
        operations_store[operation_id]["error"] = "Превышено время ожидания"

@app.route("/status/<operation_id>", methods=["GET"])
def get_operation_status(operation_id):
    """Получить статус долгосрочной операции"""
    
    if operation_id not in operations_store:
        return jsonify({"error": "Операция не найдена"}), 404
    
    operation = operations_store[operation_id]
    
    response = {
        "operation_id": operation_id,
        "status": operation["status"],
        "created_at": operation["created_at"],
        "model_name": operation["model_name"],
        "prompt": operation["prompt"]
    }
    
    if "completed_at" in operation:
        response["completed_at"] = operation["completed_at"]
    
    if "error" in operation:
        response["error"] = operation["error"]
        
    if operation["status"] == "completed" and "file_path" in operation:
        response["download_url"] = f"/download/{operation_id}"
        response["file_type"] = operation["file_type"]
    
    return jsonify(response)

@app.route("/download/<operation_id>", methods=["GET"])
def download_file(operation_id):
    """Скачать сгенерированный файл"""
    
    if operation_id not in operations_store:
        return jsonify({"error": "Операция не найдена"}), 404
    
    operation = operations_store[operation_id]
    
    if operation["status"] != "completed" or "file_path" not in operation:
        return jsonify({"error": "Файл недоступен"}), 400
    
    try:
        return send_file(
            operation["file_path"],
            mimetype=operation["file_type"],
            as_attachment=True,
            download_name=f"{operation['model_name']}_{operation_id}.mp4"
        )
    except Exception as e:
        return jsonify({"error": f"Ошибка скачивания: {str(e)}"}), 500

def handle_image_result(result, model_name, prompt):
    """Обрабатывает результат генерации изображения"""
    predictions = result.get("predictions", [])
    
    if predictions:
        prediction = predictions[0]
        
        if "bytesBase64Encoded" in prediction:
            # Сохраняем изображение во временный файл
            image_data = base64.b64decode(prediction["bytesBase64Encoded"])
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
            temp_file.write(image_data)
            temp_file.close()
            
            # Создаем операцию для изображения
            operation_id = f"img_{int(time.time())}_{hash(prompt) % 10000}"
            operations_store[operation_id] = {
                "status": "completed",
                "model_name": model_name,
                "prompt": prompt,
                "file_path": temp_file.name,
                "file_type": "image/png",
                "created_at": datetime.now().isoformat(),
                "completed_at": datetime.now().isoformat()
            }
            
            return {
                "operation_id": operation_id,
                "status": "completed",
                "type": "image",
                "download_url": f"/download/{operation_id}",
                "file_type": "image/png"
            }
        elif "gcsOutputDirectory" in prediction:
            return {
                "status": "completed",
                "type": "image", 
                "gcs_url": prediction["gcsOutputDirectory"]
            }
    
    return {"error": "Изображение не найдено в ответе"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port, debug=False) 