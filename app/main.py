from fastapi import FastAPI
from app.routers import public
from app.routers import internal as internal_router
from app.routers import admin as admin_router
from fastapi.staticfiles import StaticFiles
from app.services.asterisk import start_asterisk_monitoring, stop_asterisk_monitoring
from contextlib import asynccontextmanager
import logging

logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Управление жизненным циклом приложения"""
    # Startup
    print("=" * 50)
    print("ЗАПУСК NEURALBOT API")
    print("=" * 50)
    logger.info("Запуск NeuralBot API...")
    
    # Подключаемся к Asterisk AMI и запускаем мониторинг событий
    try:
        print("Подключение к Asterisk AMI...")
        success = await start_asterisk_monitoring()
        if success:
            print("✅ Asterisk AMI мониторинг успешно запущен!")
            logger.info("Asterisk AMI мониторинг успешно запущен")
        else:
            print("❌ Не удалось запустить Asterisk AMI мониторинг")
            logger.warning("Не удалось запустить Asterisk AMI мониторинг")
    except Exception as e:
        print(f"❌ Ошибка при запуске Asterisk мониторинга: {e}")
        logger.error(f"Ошибка при запуске Asterisk мониторинга: {e}")
    
    yield
    
    # Shutdown
    print("=" * 50)
    print("ОСТАНОВКА NEURALBOT API")
    print("=" * 50)
    logger.info("Остановка NeuralBot API...")
    
    # Отключаемся от Asterisk AMI
    try:
        await stop_asterisk_monitoring()
        print("Asterisk AMI мониторинг остановлен")
        logger.info("Asterisk AMI мониторинг остановлен")
    except Exception as e:
        print(f"Ошибка при остановке Asterisk мониторинга: {e}")
        logger.error(f"Ошибка при остановке Asterisk мониторинга: {e}")

app = FastAPI(title="NeuralBot API", lifespan=lifespan)

@app.get("/healthz")
def healthz(): return {"status": "ok"}

app.include_router(public.router, prefix="/api")
app.include_router(internal_router.router, prefix="/internal")
app.include_router(admin_router.router, prefix="/admin")
app.mount("/widget", StaticFiles(directory="web/widget", html=True), name="widget")
app.mount("/admin", StaticFiles(directory="web/admin", html=True), name="admin")
