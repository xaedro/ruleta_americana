"""
import asyncio
import json
import logging
import os
from typing import cast, Dict, List, Optional
import uuid

from fastapi import FastAPI, Request, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from pydantic import BaseModel

app = FastAPI()

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- MIDDLEWARE DE PERMISOS ---
# Middleware para Permissions-Policy y filtrado de /cron
class PermissionsPolicyMiddleware(BaseHTTPMiddleware):
	async def dispatch(self, request: Request, call_next):
		# Filtrar solicitudes al endpoint /cron
		if request.url.path == "/cron":
			user_agent = request.headers.get("User-Agent", "")
			if "cron-job.org" not in user_agent.lower():
				return {"status": "forbidden", "message": "Acceso denegado"}
		
		response = await call_next(request)
		response.headers['Permissions-Policy'] = 'display-capture=(self)'
		return response

# --- MIDDLEWARE DE CORS ---

#app.add_middleware(
#	CORSMiddleware,
#	allow_origins=["https://ruleta-americana.onrender.com"],
#	allow_credentials=True,
#	allow_methods=["*"],
#	allow_headers=["*"],
#)

app.add_middleware(
	CORSMiddleware,
	allow_origins=["https://ruletaamericana.up.railway.app"],
	allow_credentials=True,
	allow_methods=["*"],
	allow_headers=["*"],
)

# Añadir el middleware de permisos
app.add_middleware(PermissionsPolicyMiddleware)

# Montar directorio estático
app.mount("/static", StaticFiles(directory="static"), name="static")

# --- CONFIGURACIÓN Y ESTADO GLOBAL ---
SECRET_KEY = "tu_clave_secreta_super_dificil"
STREAMER_USERNAME = "mario"
streamer_ws: Optional[WebSocket] = None
streamer_is_active = False

# --- MODELOS PYDANTIC ---
class Event(BaseModel):
	content: str

class NumeroCaido(BaseModel):
	numero: int

class ConsecutiveData(BaseModel):
	consecutive_id: int

class DateTimeData(BaseModel):
	fecha_hora_str: str

# --- CONNECTION MANAGER ---
class ConnectionManager:
	def __init__(self):
		self.active_connections: Dict[str, WebSocket] = {}
		self.streamer_id: Optional[str] = None
		self.current_consecutive_id: Optional[int] = None  # Almacenar el consecutive_id actual

	async def connect(self, websocket: WebSocket) -> str:
		await websocket.accept()
		client_id = str(uuid.uuid4())
		self.active_connections[client_id] = websocket
		logger.info(f"Nuevo cliente conectado con ID: {client_id}")
		# Enviar el número de usuarios conectados a todos los clientes
		await self.broadcast_json({"type": "usuarios_conectados", "payload": len(self.active_connections)})
		# Enviar el consecutive_id actual al nuevo cliente, si existe
		if self.current_consecutive_id is not None:
			await self.send_personal_json(
				{"type": "juego_numero", "payload": self.current_consecutive_id},
				client_id
			)
		return client_id

	def disconnect(self, client_id: str):
		global streamer_ws, streamer_is_active
		if client_id in self.active_connections:
			del self.active_connections[client_id]
			logger.info(f"Cliente {client_id} desconectado.")
		
		if client_id == self.streamer_id:
			logger.info("El streamer se ha desconectado.")
			streamer_ws = None
			streamer_is_active = False
			self.streamer_id = None
			loop = asyncio.get_event_loop()
			if loop.is_running():
				loop.create_task(self.broadcast_json({"type": "stream_ended"}))
		
		# Enviar el número de usuarios conectados a todos los clientes
		loop = asyncio.get_event_loop()
		if loop.is_running():
			loop.create_task(self.broadcast_json({"type": "usuarios_conectados", "payload": len(self.active_connections)}))

	async def broadcast_json(self, data: dict):
		message = json.dumps(data)
		for client_id, connection in list(self.active_connections.items()):
			try:
				await connection.send_text(message)
			except Exception as e:
				logger.error(f"Error enviando mensaje a {client_id}: {str(e)}")
				self.disconnect(client_id)
	
	async def send_personal_json(self, data: dict, client_id: str):
		if client_id in self.active_connections:
			try:
				await self.active_connections[client_id].send_text(json.dumps(data))
			except Exception as e:
				logger.error(f"Error enviando mensaje personal a {client_id}: {str(e)}")
				self.disconnect(client_id)

manager = ConnectionManager()

"""
@app.get("/cron")
async def cron():
	return {"status": "ok"}
"""

# --- ENDPOINTS HTTP ---
@app.get("/old")
async def get_old_version():
	return FileResponse(os.path.join(os.path.dirname(__file__), "index3.html"))

@app.get("/")
async def get_new_version():
	return FileResponse(os.path.join(os.path.dirname(__file__), "index.html"))

@app.post("/stream_ended")
async def notify_stream_ended(x_secret_key: Optional[str] = Header(None)):
	if x_secret_key != SECRET_KEY:
		raise HTTPException(status_code=403, detail="Clave secreta inválida")
	if manager.streamer_id:
		manager.disconnect(manager.streamer_id)
	return {"status": "ok"}

# --- ENDPOINT WEBSOCKET ---
@app.websocket("/ws/users")
async def websocket_users(websocket: WebSocket):
	global streamer_ws, streamer_is_active
	client_id = await manager.connect(websocket)
	
	try:
		while True:
			data_str = await websocket.receive_text()
			data = json.loads(data_str)
			msg_type = data.get("type")
			logger.info(f"Mensaje recibido de {client_id}: {data}")

			if msg_type == "ping":
				await websocket.send_text(json.dumps({"type": "pong"}))
				continue

			elif msg_type == "login":
				username = data.get("name", "").lower()
				if username == STREAMER_USERNAME and not manager.streamer_id:
					manager.streamer_id = client_id
					streamer_ws = websocket
					logger.info(f"Asignando streamer_id: {client_id} para usuario {username}")
					await manager.send_personal_json({
						"type": "login_success",
						"role": "streamer",
						"streamer_id": client_id,
						"client_id": client_id
					}, client_id)
				else:
					await manager.send_personal_json({
						"type": "login_success",
						"role": "viewer",
						"streamer_id": manager.streamer_id or '',
						"client_id": client_id
					}, client_id)
					if streamer_is_active:
						await manager.send_personal_json({"type": "stream_started"}, client_id)

			elif msg_type == "offer":
				if manager.streamer_id:
					logger.info(f"Reenviando oferta de {client_id} a streamer {manager.streamer_id}")
					data['from_id'] = client_id
					await manager.send_personal_json(data, manager.streamer_id)

			elif msg_type == "answer":
				target_id = data.get("target_id")
				if target_id:
					logger.info(f"Reenviando respuesta de {client_id} a {target_id}")
					data['from_id'] = client_id
					await manager.send_personal_json(data, target_id)

			elif msg_type == "candidate":
				target_id = data.get("target_id")
				if target_id:
					logger.info(f"Reenviando candidato de {client_id} a {target_id}")
					data['from_id'] = client_id
					await manager.send_personal_json(data, target_id)

			elif msg_type == "start_stream":
				if client_id == manager.streamer_id:
					streamer_is_active = True
					#await manager.broadcast_json({"type": "stream_started"})
					# Reenviar mensaje con dimensiones del video
					await manager.broadcast_json({
							"type": "stream_started",
							"video_dimensions": data.get("video_dimensions", {})
					})

					logger.info("El streamer ha iniciado la transmisión.")
			
			elif msg_type == "stream_ended":
				if client_id == manager.streamer_id:
					manager.disconnect(client_id)
			elif msg_type == "game_event":
				if client_id == manager.streamer_id:
					content = data.get("content")
					if content in ["game_started", "game_ended", "bets_open", "bets_closed"]:
						await manager.broadcast_json({
							"type": "game_event",
							"payload": content
						})
						logger.info(f"Evento de juego enviado por {client_id}: {content}")

			elif msg_type == "fecha_hora":
				if client_id == manager.streamer_id:
					await manager.broadcast_json({
						"type": "fecha_hora",
						"payload": data.get("fecha_hora_str")
					})
					logger.info(f"Fecha y hora enviada por {client_id}: {data.get('fecha_hora_str')}")

			elif msg_type == "consecutivo_juego":
				if client_id == manager.streamer_id:
					manager.current_consecutive_id = data.get("consecutive_id")
					await manager.broadcast_json({
						"type": "juego_numero",
						"payload": data.get("consecutive_id")
					})
					logger.info(f"Consecutivo enviado por {client_id}: {data.get('consecutive_id')}")

			elif msg_type == "numero_caido":
				if client_id == manager.streamer_id:
					await manager.broadcast_json({
						"type": "numero_caido",
						"payload": data.get("numero")
					})
					logger.info(f"Número caído enviado por {client_id}: {data.get('numero')}")
			
	except WebSocketDisconnect as e:
		logger.info(f"Cliente {client_id} desconectado con código {e.code}: {e.reason}")
		manager.disconnect(client_id)
	except (ValueError, json.JSONDecodeError) as e:
		logger.error(f"Error de formato de mensaje del cliente {client_id}: {str(e)}")
	except Exception as e:
		logger.error(f"Error inesperado en WebSocket para {client_id}: {str(e)}")
		manager.disconnect(client_id)
"""

import asyncio
import json
import logging
import os
from typing import Dict, List, Optional
import uuid

from fastapi import FastAPI, Request, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

app = FastAPI()

# Configurar logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- MIDDLEWARE DE PERMISOS ---
class PermissionsPolicyMiddleware:
    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/cron":
            user_agent = request.headers.get("User-Agent", "")
            if "cron-job.org" not in user_agent.lower():
                return {"status": "forbidden", "message": "Acceso denegado"}
        response = await call_next(request)
        response.headers['Permissions-Policy'] = 'display-capture=(self)'
        return response

# --- MIDDLEWARE DE CORS ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://ruletaamericana.up.railway.app",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Añadir el middleware de permisos
app.add_middleware(PermissionsPolicyMiddleware)

# Montar directorio estático
app.mount("/static", StaticFiles(directory="static"), name="static")

# --- CONFIGURACIÓN Y ESTADO GLOBAL ---
SECRET_KEY = "tu_clave_secreta_super_dificil"
STREAMER_USERNAME = "mario"
streamer_ws: Optional[WebSocket] = None
streamer_is_active = False

# --- MODELOS PYDANTIC ---
class ConsecutiveData(BaseModel):
    consecutive_id: int

class DateTimeData(BaseModel):
    fecha_hora_str: str

# --- CONNECTION MANAGER ---
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.streamer_id: Optional[str] = None
        self.current_consecutive_id: Optional[int] = None

    async def connect(self, websocket: WebSocket) -> str:
        await websocket.accept()
        client_id = str(uuid.uuid4())
        self.active_connections[client_id] = websocket
        logger.info(f"Nuevo cliente conectado con ID: {client_id}")
        await self.broadcast_json({"type": "usuarios_conectados", "payload": len(self.active_connections)})
        if self.current_consecutive_id is not None:
            await self.send_personal_json(
                {"type": "juego_numero", "payload": self.current_consecutive_id},
                client_id
            )
        return client_id

    def disconnect(self, client_id: str):
        global streamer_ws, streamer_is_active
        if client_id in self.active_connections:
            del self.active_connections[client_id]
            logger.info(f"Cliente {client_id} desconectado.")
            # Notificar al streamer si un espectador se desconecta
            if client_id != self.streamer_id and self.streamer_id:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(self.send_personal_json(
                        {"type": "viewer_disconnected", "viewer_id": client_id},
                        self.streamer_id
                    ))
        if client_id == self.streamer_id:
            logger.info("El streamer se ha desconectado.")
            streamer_ws = None
            streamer_is_active = False
            self.streamer_id = None
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(self.broadcast_json({"type": "stream_ended"}))
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(self.broadcast_json({"type": "usuarios_conectados", "payload": len(self.active_connections)}))

    async def broadcast_json(self, data: dict):
        message = json.dumps(data)
        for client_id, connection in list(self.active_connections.items()):
            try:
                await connection.send_text(message)
            except Exception as e:
                logger.error(f"Error enviando mensaje a {client_id}: {str(e)}")
                self.disconnect(client_id)

    async def send_personal_json(self, data: dict, client_id: str):
        if client_id in self.active_connections:
            try:
                await self.active_connections[client_id].send_text(json.dumps(data))
            except Exception as e:
                logger.error(f"Error enviando mensaje personal a {client_id}: {str(e)}")
                self.disconnect(client_id)

manager = ConnectionManager()

# --- ENDPOINTS HTTP ---
@app.get("/old")
async def get_old_version():
    return FileResponse(os.path.join(os.path.dirname(__file__), "index3.html"))

@app.get("/")
async def get_new_version():
    return FileResponse(os.path.join(os.path.dirname(__file__), "index.html"))

@app.post("/stream_ended")
async def notify_stream_ended(x_secret_key: Optional[str] = Header(None)):
    if x_secret_key != SECRET_KEY:
        raise HTTPException(status_code=403, detail="Clave secreta inválida")
    if manager.streamer_id:
        manager.disconnect(manager.streamer_id)
    return {"status": "ok"}

# --- ENDPOINT WEBSOCKET ---
@app.websocket("/ws/users")
async def websocket_users(websocket: WebSocket):
    global streamer_ws, streamer_is_active
    client_id = await manager.connect(websocket)

    try:
        while True:
            data_str = await websocket.receive_text()
            data = json.loads(data_str)
            msg_type = data.get("type")
            logger.info(f"Mensaje recibido de {client_id}: {data}")

            if msg_type == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
                continue

            elif msg_type == "login":
                username = data.get("name", "").lower()
                if username == STREAMER_USERNAME and not manager.streamer_id:
                    manager.streamer_id = client_id
                    streamer_ws = websocket
                    logger.info(f"Asignando streamer_id: {client_id} para usuario {username}")
                    await manager.send_personal_json({
                        "type": "login_success",
                        "role": "streamer",
                        "streamer_id": client_id,
                        "client_id": client_id
                    }, client_id)
                else:
                    await manager.send_personal_json({
                        "type": "login_success",
                        "role": "viewer",
                        "streamer_id": manager.streamer_id or '',
                        "client_id": client_id
                    }, client_id)
                if streamer_is_active:
                    await manager.send_personal_json({"type": "stream_started"}, client_id)

            elif msg_type == "offer":
                if manager.streamer_id:
                    logger.info(f"Reenviando oferta de {client_id} a streamer {manager.streamer_id}")
                    data['from_id'] = client_id
                    await manager.send_personal_json(data, manager.streamer_id)

            elif msg_type == "answer":
                target_id = data.get("target_id")
                if target_id:
                    logger.info(f"Reenviando respuesta de {client_id} a {target_id}")
                    data['from_id'] = client_id
                    await manager.send_personal_json(data, target_id)

            elif msg_type == "candidate":
                target_id = data.get("target_id")
                if target_id:
                    logger.info(f"Reenviando candidato de {client_id} a {target_id}")
                    data['from_id'] = client_id
                    await manager.send_personal_json(data, target_id)

            elif msg_type == "start_stream":
                if client_id == manager.streamer_id:
                    streamer_is_active = True
                    await manager.broadcast_json({
                        "type": "stream_started",
                        "video_dimensions": data.get("video_dimensions", {})
                    })
                    logger.info("El streamer ha iniciado la transmisión.")

            elif msg_type == "stream_ended":
                if client_id == manager.streamer_id:
                    manager.disconnect(client_id)

            elif msg_type == "game_event":
                if client_id == manager.streamer_id:
                    content = data.get("content")
                    if content in ["game_started", "game_ended", "bets_open", "bets_closed"]:
                        await manager.broadcast_json({
                            "type": "game_event",
                            "payload": content
                        })
                        logger.info(f"Evento de juego enviado por {client_id}: {content}")

            elif msg_type == "fecha_hora":
                if client_id == manager.streamer_id:
                    await manager.broadcast_json({
                        "type": "fecha_hora",
                        "payload": data.get("fecha_hora_str")
                    })
                    logger.info(f"Fecha y hora enviada por {client_id}: {data.get('fecha_hora_str')}")

            elif msg_type == "consecutivo_juego":
                if client_id == manager.streamer_id:
                    manager.current_consecutive_id = data.get("consecutive_id")
                    await manager.broadcast_json({
                        "type": "juego_numero",
                        "payload": data.get("consecutive_id")
                    })
                    logger.info(f"Consecutivo enviado por {client_id}: {data.get('consecutive_id')}")

            elif msg_type == "numero_caido":
                if client_id == manager.streamer_id:
                    await manager.broadcast_json({
                        "type": "numero_caido",
                        "payload": data.get("numero")
                    })
                    logger.info(f"Número caído enviado por {client_id}: {data.get('numero')}")

            elif msg_type == "apuesta":
                if client_id != manager.streamer_id:
                    await manager.broadcast_json({
                        "type": "apuesta",
                        "payload": {"username": data.get("username"), "numero": data.get("numero")}
                    })
                    logger.info(f"Apuesta de {data.get('username')}: {data.get('numero')}")

    except WebSocketDisconnect as e:
        logger.info(f"Cliente {client_id} desconectado con código {e.code}: {e.reason}")
        manager.disconnect(client_id)
    except (ValueError, json.JSONDecodeError) as e:
        logger.error(f"Error de formato de mensaje del cliente {client_id}: {str(e)}")
    except Exception as e:
        logger.error(f"Error inesperado en WebSocket para {client_id}: {str(e)}")
        manager.disconnect(client_id)

