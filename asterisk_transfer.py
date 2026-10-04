"""
Сигнал перевода звонка после реплики бота.

Основной режим (sip): фиксируем целевой 6-значный номер для обратного SIP INVITE
на стороне Инфолады (новый независимый вызов без HTTP API).

Legacy режим (ami): исторический AMI Redirect (оставлен для обратной совместимости).
"""

import logging
import random
import socket
from typing import Optional

logger = logging.getLogger(__name__)

# Коды событий AMI (упрощённый парсинг ответа)
AMI_SUCCESS = "Success"
AMI_ERROR = "Error"


def _parse_ami_block(block_bytes: bytes) -> dict[str, str]:
    """Парсит один AMI-блок (строки Key: Value, разделитель \\r\\n\\r\\n)."""
    text = block_bytes.decode("utf-8", errors="replace").replace("\r\n", "\n")
    result: dict[str, str] = {}
    for line in text.split("\n"):
        line = line.strip()
        if ":" in line:
            k, _, v = line.partition(":")
            result[k.strip()] = v.strip()
    return result


class _AmiBufferedReader:
    """
    Чтение AMI без потери данных, если несколько блоков приходят в одном recv().
    Обязательно для DBGet: Response: Success идёт отдельно от Event: DBGetResponse с Val.
    """

    def __init__(self, sock: socket.socket, timeout: float = 5.0):
        self.sock = sock
        self._buf = bytearray()
        self.sock.settimeout(timeout)

    def read_block(self) -> dict[str, str]:
        sep = b"\r\n\r\n"
        while sep not in self._buf:
            try:
                chunk = self.sock.recv(65536)
            except (socket.timeout, BlockingIOError):
                break
            if not chunk:
                break
            self._buf.extend(chunk)
        if sep not in self._buf:
            return {}
        idx = self._buf.index(sep)
        block_bytes = bytes(self._buf[:idx])
        del self._buf[: idx + len(sep)]
        return _parse_ami_block(block_bytes)


def _read_ami_response(sock: socket.socket, timeout: float = 5.0) -> dict:
    """Читает один AMI-блок с сокета (устаревший путь; предпочтительно _AmiBufferedReader)."""
    return _AmiBufferedReader(sock, timeout).read_block()


def _read_ami_until_response_reader(
    reader: _AmiBufferedReader, max_blocks: int = 64
) -> dict[str, str]:
    """
    Читает блоки AMI до первого с ключом Response (результат Action).
    Пропускает приветствие Manager, Event: FullyBooted и прочие события без Response.
    """
    for _ in range(max_blocks):
        block = reader.read_block()
        if block and "Response" in block:
            return block
        if not block:
            break
    return {}


def _read_ami_until_response(sock: socket.socket, timeout: float = 5.0, max_blocks: int = 64) -> dict:
    """Обёртка: один буферизованный читатель на вызов (для совместимости)."""
    return _read_ami_until_response_reader(_AmiBufferedReader(sock, timeout), max_blocks)


def _parse_allowed_targets(raw: str) -> set[str]:
    return {x.strip() for x in (raw or "").split(",") if x.strip()}


def _is_valid_target_number(target: Optional[str], allowed: set[str]) -> bool:
    if not target:
        return False
    num = str(target).strip()
    if len(num) != 6 or not num.isdigit():
        return False
    return (not allowed) or (num in allowed)


def _read_ami_raw_until_marker(
    sock: socket.socket, end_marker: str, timeout: float = 8.0, max_bytes: int = 524288
) -> str:
    """Читает поток AMI до появления подстроки (например CoreShowChannelsComplete)."""
    sock.settimeout(timeout)
    data = bytearray()
    marker_b = end_marker.encode("ascii", errors="ignore")
    while len(data) < max_bytes:
        try:
            chunk = sock.recv(65536)
            if not chunk:
                break
            data.extend(chunk)
            if marker_b in data:
                break
        except socket.timeout:
            logger.warning("AMI: таймаут чтения до маркера %r", end_marker)
            break
    return data.decode("utf-8", errors="replace")


def _ami_blocks_to_dicts(text: str) -> list[dict[str, str]]:
    """Разбивает ответ AMI на блоки key:value (разделитель пустая строка)."""
    text = text.replace("\r\n", "\n")
    blocks: list[dict[str, str]] = []
    for raw in text.split("\n\n"):
        raw = raw.strip()
        if not raw:
            continue
        d: dict[str, str] = {}
        for line in raw.split("\n"):
            line = line.strip()
            if ":" in line:
                k, _, v = line.partition(":")
                d[k.strip()] = v.strip()
        if d:
            blocks.append(d)
    return blocks


def _uuid_substrings_for_match(hex_key: str, call_uuid_display: Optional[str]) -> set[str]:
    """Подстроки для сопоставления с ApplicationData AudioSocket."""
    out: set[str] = set()
    h = (hex_key or "").strip().lower()
    if len(h) == 32 and all(c in "0123456789abcdef" for c in h):
        out.add(h)
    if call_uuid_display:
        d = call_uuid_display.strip()
        if d:
            out.add(d.lower())
            compact = "".join(c for c in d if c in "0123456789abcdefABCDEF")
            if len(compact) == 32:
                out.add(compact.lower())
    return out


def _ami_coreshow_find_channel(
    sock: socket.socket, hex_key: str, call_uuid_display: Optional[str]
) -> Optional[str]:
    """
    Если AstDB пуст, ищем канал с Application=AudioSocket, в ApplicationData которого есть наш UUID.
    """
    sock.sendall(b"Action: CoreShowChannels\r\n\r\n")
    raw = _read_ami_raw_until_marker(sock, "CoreShowChannelsComplete")
    blocks = _ami_blocks_to_dicts(raw)
    needles = _uuid_substrings_for_match(hex_key, call_uuid_display)
    if not needles:
        logger.warning("AMI CoreShowChannels: нечего сопоставлять (нет hex ключа и call_uuid)")
        return None

    def hex_only(s: str) -> str:
        return "".join(c for c in s.lower() if c in "0123456789abcdef")

    audio_rows: list[dict[str, str]] = []
    matches: list[str] = []

    for ev in blocks:
        if (ev.get("Event") or "").strip() != "CoreShowChannel":
            continue
        app = (ev.get("Application") or "").strip()
        appdata = ev.get("ApplicationData") or ""
        chan = (ev.get("Channel") or "").strip()
        if not chan:
            continue
        if app.lower() == "audiosocket":
            audio_rows.append(ev)
        ad_l = appdata.lower()
        ad_hex = hex_only(appdata)
        hit = False
        for n in needles:
            if not n:
                continue
            nl = n.lower()
            if len(nl) == 32 and all(c in "0123456789abcdef" for c in nl):
                if nl in ad_hex:
                    hit = True
                    break
            if nl in ad_l:
                hit = True
                break
        if hit and chan not in matches:
            matches.append(chan)

    if len(matches) == 1:
        logger.info("AMI CoreShowChannels: найден канал %r по UUID в ApplicationData", matches[0])
        return matches[0]
    if len(matches) > 1:
        logger.warning("AMI CoreShowChannels: несколько каналов по UUID %s: %s", hex_key, matches)
        return None

    # Резерв: ровно один канал с AudioSocket (один звонок на бота)
    audiosocket_chans = [
        (ev.get("Channel") or "").strip()
        for ev in audio_rows
        if (ev.get("Channel") or "").strip()
    ]
    if len(audiosocket_chans) == 1:
        logger.warning(
            "AMI CoreShowChannels: единственный AudioSocket-канал без совпадения UUID (риск при параллельных звонках): %r",
            audiosocket_chans[0],
        )
        return audiosocket_chans[0]

    logger.warning(
        "AMI CoreShowChannels: канал не найден (matches=0, AudioSocket=%s). Проверьте Set DB / func_db.",
        len(audiosocket_chans),
    )
    return None


def _ami_coreshow_find_channel_reader(
    reader: _AmiBufferedReader, hex_key: str, call_uuid_display: Optional[str]
) -> Optional[str]:
    """
    То же, что _ami_coreshow_find_channel, но чтение только через reader.read_block —
    без сырого recv на том же сокете (иначе ломается буфер AMI).
    """
    reader.sock.sendall(b"Action: CoreShowChannels\r\n\r\n")
    blocks: list[dict[str, str]] = []
    for _ in range(4096):
        block = reader.read_block()
        if not block:
            break
        blocks.append(block)
        if (block.get("Event") or "").strip() == "CoreShowChannelsComplete":
            break
    needles = _uuid_substrings_for_match(hex_key, call_uuid_display)
    if not needles:
        logger.warning("AMI CoreShowChannels(reader): нечего сопоставлять")
        return None

    def hex_only(s: str) -> str:
        return "".join(c for c in s.lower() if c in "0123456789abcdef")

    audio_rows: list[dict[str, str]] = []
    matches: list[str] = []

    for ev in blocks:
        if (ev.get("Event") or "").strip() != "CoreShowChannel":
            continue
        app = (ev.get("Application") or "").strip()
        appdata = ev.get("ApplicationData") or ""
        chan = (ev.get("Channel") or "").strip()
        if not chan:
            continue
        if app.lower() == "audiosocket":
            audio_rows.append(ev)
        ad_l = appdata.lower()
        ad_hex = hex_only(appdata)
        hit = False
        for n in needles:
            if not n:
                continue
            nl = n.lower()
            if len(nl) == 32 and all(c in "0123456789abcdef" for c in nl):
                if nl in ad_hex:
                    hit = True
                    break
            if nl in ad_l:
                hit = True
                break
        if hit and chan not in matches:
            matches.append(chan)

    if len(matches) == 1:
        logger.info("AMI CoreShowChannels(reader): найден канал %r", matches[0])
        return matches[0]
    if len(matches) > 1:
        logger.warning("AMI CoreShowChannels(reader): несколько каналов: %s", matches)
        return None
    audiosocket_chans = [
        (ev.get("Channel") or "").strip()
        for ev in audio_rows
        if (ev.get("Channel") or "").strip()
    ]
    if len(audiosocket_chans) == 1:
        logger.warning(
            "AMI CoreShowChannels(reader): единственный AudioSocket без UUID-совпадения: %r",
            audiosocket_chans[0],
        )
        return audiosocket_chans[0]
    logger.warning(
        "AMI CoreShowChannels(reader): канал не найден (AudioSocket=%s)", len(audiosocket_chans)
    )
    return None


def _ami_redirect_response_channel_missing(block: Optional[dict]) -> bool:
    if not block:
        return False
    if (block.get("Response") or "").strip() != AMI_ERROR:
        return False
    msg = (block.get("Message") or "").lower()
    return (
        "does not exist" in msg
        or "no such channel" in msg
        or "nosuchchannel" in msg.replace(" ", "")
    )


def _ami_do_redirect(
    reader: _AmiBufferedReader, real_channel: str, ctx: str, ext: str
) -> tuple[bool, Optional[dict]]:
    """Отправляет Redirect и ждёт Response Success/Error с ActionID. Возвращает (успех, блок ответа)."""
    redir_id = f"rd{random.randint(10**8, 10**12)}"
    reader.sock.sendall(
        (
            f"Action: Redirect\r\n"
            f"ActionID: {redir_id}\r\n"
            f"Channel: {real_channel}\r\n"
            f"Context: {ctx}\r\n"
            f"Exten: {ext}\r\n"
            f"Priority: 1\r\n"
            f"\r\n"
        ).encode("utf-8")
    )
    last_match: Optional[dict] = None
    for _ in range(256):
        block = reader.read_block()
        if not block:
            logger.warning("AMI Redirect: обрыв AMI при ожидании ответа (канал %r)", real_channel)
            return False, last_match
        resp = (block.get("Response") or "").strip()
        if resp not in (AMI_SUCCESS, AMI_ERROR):
            continue
        bid = (block.get("ActionID") or "").strip()
        # Только ответ с тем же ActionID, что у Redirect; иначе можно принять чужой Success из потока AMI.
        if bid != redir_id:
            continue
        last_match = block
        success = resp == AMI_SUCCESS
        if not success:
            logger.warning("AMI Redirect failed: %s", block)
        return success, block
    logger.warning("AMI Redirect: нет ответа Success/Error с ActionID=%s для канала %r", redir_id, real_channel)
    return False, last_match


def _ami_fresh_coreshow_redirect(
    host: str,
    port: int,
    user: str,
    secret: str,
    hex_key: str,
    call_uuid_display: Optional[str],
    ctx: str,
    ext: str,
) -> bool:
    """
    Новое AMI-соединение: только CoreShowChannels (через буфер) + Redirect.
    Используется, если AstDB дала устаревшее имя канала (Channel does not exist).
    """
    sock: Optional[socket.socket] = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(12.0)
        sock.connect((host, port))
        reader = _AmiBufferedReader(sock, timeout=12.0)
        reader.sock.sendall(
            f"Action: Login\r\n"
            f"Username: {user}\r\n"
            f"Secret: {secret}\r\n"
            f"\r\n".encode("utf-8")
        )
        login_resp = _read_ami_until_response_reader(reader)
        if login_resp.get("Response") != AMI_SUCCESS:
            logger.warning("AMI retry (CoreShow): Login failed %s", login_resp)
            return False
        found = _ami_coreshow_find_channel_reader(reader, hex_key, call_uuid_display)
        if not found:
            logger.warning("AMI retry (CoreShow): канал не найден для ключа %r", hex_key)
            return False
        logger.info("AMI retry (CoreShow): повторный Redirect на канал %r", found)
        success, _ = _ami_do_redirect(reader, found, ctx, ext)
        return success
    except Exception as e:
        logger.exception("AMI retry (CoreShow): %s", e)
        return False
    finally:
        if sock is not None:
            try:
                sock.sendall(b"Action: Logoff\r\n\r\n")
            except Exception:
                pass
            try:
                sock.close()
            except Exception:
                pass


def _ami_dbget_channel(reader: _AmiBufferedReader, family: str, key: str) -> Optional[str]:
    """
    Читает из AstDB имя канала Asterisk (Val) по Family/Key.

    Между Login и ответом на DBGet в потоке часто идут асинхронные Event (Newchannel,
    VarSet и т.д.). Нельзя ожидать, что сразу после DBGet первый блок — Response: Success.
    Шлём ActionID и читаем блоки до Event: DBGetResponse (с тем же ActionID, если есть).
    """
    action_id = f"vk{random.randint(10**8, 10**12)}"
    reader.sock.sendall(
        (
            f"Action: DBGet\r\n"
            f"ActionID: {action_id}\r\n"
            f"Family: {family}\r\n"
            f"Key: {key}\r\n"
            f"\r\n"
        ).encode("utf-8")
    )
    for _ in range(256):
        block = reader.read_block()
        if not block:
            logger.warning("AMI DBGet: обрыв AMI при ожидании DBGetResponse, key=%r", key)
            return None
        ev = (block.get("Event") or "").strip()
        if ev == "DBGetResponse":
            bid = (block.get("ActionID") or "").strip()
            if bid and bid != action_id:
                continue
            val = (block.get("Val") or "").strip()
            if not val:
                logger.warning(
                    "AMI DBGet: пустой Val (нет записи AstDB или несовпадение ключа) family=%r key=%r",
                    family,
                    key,
                )
                return None
            logger.info("AMI DBGet: channel=%r for key=%r", val, key)
            return val
        resp = (block.get("Response") or "").strip()
        if resp == AMI_ERROR:
            bid = (block.get("ActionID") or "").strip()
            if bid and bid != action_id:
                continue
            logger.warning("AMI DBGet failed: family=%r key=%r resp=%s", family, key, block)
            return None
        if resp == AMI_SUCCESS:
            bid = (block.get("ActionID") or "").strip()
            if bid and bid != action_id:
                continue
            continue
        continue
    logger.warning("AMI DBGet: нет DBGetResponse за 256 блоков, key=%r", key)
    return None


def _request_transfer_via_sip(channel_id: str, target_number: str) -> bool:
    """
    SIP-only режим: bot-side фиксирует и валидирует цель перевода.
    Фактический встречный INVITE инициируется на стороне Инфолады.
    """
    try:
        from config import INFOLADA_ALLOWED_TARGETS
    except ImportError:
        INFOLADA_ALLOWED_TARGETS = "697070,696157,697777,695873,695874"

    allowed = _parse_allowed_targets(INFOLADA_ALLOWED_TARGETS)
    if not _is_valid_target_number(target_number, allowed):
        logger.warning("Transfer SIP blocked: invalid or not-allowed target %r", target_number)
        return False
    if not channel_id:
        logger.warning("Transfer SIP skipped: empty channel_id")
        return False

    # Здесь не делаем HTTP/AMI: по согласованию обратный INVITE инициирует Инфолада.
    logger.info("Transfer SIP signal accepted: call_id=%s target=%s", channel_id, target_number)
    return True


def _request_transfer_via_ami(
    channel_id: Optional[str],
    context: Optional[str],
    exten: Optional[str],
    call_uuid_display: Optional[str] = None,
) -> bool:
    try:
        from config import (
            ASTERISK_AMI_HOST,
            ASTERISK_AMI_PORT,
            ASTERISK_AMI_USER,
            ASTERISK_AMI_SECRET,
            ASTERISK_TRANSFER_CONTEXT,
            ASTERISK_TRANSFER_EXTEN,
        )
    except ImportError:
        logger.debug("config не найден, перевод по AMI отключён")
        return False

    if not ASTERISK_AMI_HOST or not ASTERISK_AMI_USER or not channel_id:
        return False

    ctx = context or ASTERISK_TRANSFER_CONTEXT
    ext = exten or ASTERISK_TRANSFER_EXTEN

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10.0)
        sock.connect((ASTERISK_AMI_HOST, ASTERISK_AMI_PORT))
        reader = _AmiBufferedReader(sock, timeout=10.0)

        # Login
        reader.sock.sendall(
            f"Action: Login\r\n"
            f"Username: {ASTERISK_AMI_USER}\r\n"
            f"Secret: {ASTERISK_AMI_SECRET}\r\n"
            f"\r\n".encode("utf-8")
        )
        login_resp = _read_ami_until_response_reader(reader)
        if login_resp.get("Response") != AMI_SUCCESS:
            logger.warning("AMI Login failed: %s", login_resp)
            sock.close()
            return False

        # UUID из AudioSocket ≠ имя канала; в extensions.conf пишем CHANNEL(name) в AstDB
        real_channel = _ami_dbget_channel(reader, "vikingi_xfer", channel_id)
        if not real_channel:
            logger.warning(
                "AMI DBGet: нет имени канала для ключа %r — пробуем CoreShowChannels по UUID",
                channel_id,
            )
            real_channel = _ami_coreshow_find_channel(sock, channel_id, call_uuid_display)
        if not real_channel:
            logger.warning(
                "AMI: не удалось получить имя канала (AstDB и CoreShowChannels), key=%r. "
                "Проверьте load func_db.so и Set(DB(...)) в extensions.conf перед AudioSocket.",
                channel_id,
            )
            sock.sendall(b"Action: Logoff\r\n\r\n")
            sock.close()
            return False

        success, err_block = _ami_do_redirect(reader, real_channel, ctx, ext)
        if (
            not success
            and _ami_redirect_response_channel_missing(err_block)
            and channel_id
        ):
            reader.sock.sendall(b"Action: Logoff\r\n\r\n")
            sock.close()
            logger.info(
                "AMI: канал %r не существует — повтор через CoreShowChannels на новом соединении",
                real_channel,
            )
            return _ami_fresh_coreshow_redirect(
                ASTERISK_AMI_HOST,
                ASTERISK_AMI_PORT,
                ASTERISK_AMI_USER,
                ASTERISK_AMI_SECRET,
                channel_id,
                call_uuid_display,
                ctx,
                ext,
            )

        reader.sock.sendall(b"Action: Logoff\r\n\r\n")
        sock.close()

        return success
    except Exception as e:
        logger.exception("Ошибка при отправке AMI Redirect: %s", e)
        return False


def request_transfer_to_admin(
    channel_id: Optional[str] = None,
    context: Optional[str] = None,
    exten: Optional[str] = None,
    call_uuid_display: Optional[str] = None,
) -> bool:
    """
    Инициирует перевод звонка после WAV перевода.

    По умолчанию используется режим sip (без HTTP, по согласованному SIP сценарию).
    В legacy режиме возможно выполнение AMI Redirect.
    """
    try:
        from config import ASTERISK_TRANSFER_MODE, ASTERISK_TRANSFER_TARGET_DEFAULT
    except ImportError:
        ASTERISK_TRANSFER_MODE = "sip"
        ASTERISK_TRANSFER_TARGET_DEFAULT = "697070"

    target = (exten or ASTERISK_TRANSFER_TARGET_DEFAULT or "").strip()
    mode = (ASTERISK_TRANSFER_MODE or "sip").strip().lower()

    if mode == "ami" or mode == "sip":
        # Используем локальный AMI для Redirect на server7
        return _request_transfer_via_ami(
            channel_id=channel_id,
            context=context,
            exten=target,
            call_uuid_display=call_uuid_display,
        )


def get_exten_for_wav(wav_file: Optional[str]) -> Optional[str]:
    """
    Возвращает целевой 6-значный номер перевода по WAV-файлу.
    Значения задаются через env-переменные в config.py.
    """
    try:
        from config import (
            ASTERISK_TRANSFER_TARGET_DEFAULT,
            ASTERISK_TRANSFER_TARGET_ADMIN,
            ASTERISK_TRANSFER_TARGET_USED_CARS,
            ASTERISK_TRANSFER_TARGET_CHERY_TENET,
            ASTERISK_TRANSFER_TARGET_SERVICE,
            ASTERISK_TRANSFER_TARGET_SERVICE_ASSISTANT,
            ASTERISK_TRANSFER_TARGET_BODY_REPAIR,
            ASTERISK_TRANSFER_TARGET_PARTS,
        )
    except ImportError:
        ASTERISK_TRANSFER_TARGET_DEFAULT = "697070"
        ASTERISK_TRANSFER_TARGET_ADMIN = "697070"
        ASTERISK_TRANSFER_TARGET_USED_CARS = "696157"
        ASTERISK_TRANSFER_TARGET_CHERY_TENET = "697070"
        ASTERISK_TRANSFER_TARGET_SERVICE = "697777"
        ASTERISK_TRANSFER_TARGET_SERVICE_ASSISTANT = "697777"
        ASTERISK_TRANSFER_TARGET_BODY_REPAIR = "695873"
        ASTERISK_TRANSFER_TARGET_PARTS = "695874"

    mapping = {
        "03_transfer_admin.wav": ASTERISK_TRANSFER_TARGET_ADMIN,
        "04_transfer_used_cars.wav": ASTERISK_TRANSFER_TARGET_USED_CARS,
        "05_transfer_chery_tenet.wav": ASTERISK_TRANSFER_TARGET_CHERY_TENET,
        "10_transfer_master.wav": ASTERISK_TRANSFER_TARGET_SERVICE,
        "16_transfer_service_assistant.wav": ASTERISK_TRANSFER_TARGET_SERVICE_ASSISTANT,
        "13_transfer_body_repair.wav": ASTERISK_TRANSFER_TARGET_BODY_REPAIR,
        "11_transfer_parts.wav": ASTERISK_TRANSFER_TARGET_PARTS,
    }

    if not wav_file:
        return ASTERISK_TRANSFER_TARGET_DEFAULT
    return mapping.get(wav_file, ASTERISK_TRANSFER_TARGET_DEFAULT)


def fetch_caller_ani_by_session_key(hex_key: str) -> Optional[str]:
    """
    Номер звонящего, записанный в AstDB до AudioSocket (extensions.conf: vikingi_ani/<UUID_KEY>).

    Тот же AMI, что и для перевода; при пустом ASTERISK_AMI_HOST — None.
    """
    key = (hex_key or "").strip().lower()
    if not key:
        return None
    try:
        from config import (
            ASTERISK_AMI_HOST,
            ASTERISK_AMI_PORT,
            ASTERISK_AMI_USER,
            ASTERISK_AMI_SECRET,
        )
    except ImportError:
        return None

    if not ASTERISK_AMI_HOST or not ASTERISK_AMI_USER:
        return None

    sock: Optional[socket.socket] = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(8.0)
        sock.connect((ASTERISK_AMI_HOST, ASTERISK_AMI_PORT))
        reader = _AmiBufferedReader(sock, timeout=8.0)
        reader.sock.sendall(
            f"Action: Login\r\n"
            f"Username: {ASTERISK_AMI_USER}\r\n"
            f"Secret: {ASTERISK_AMI_SECRET}\r\n"
            f"\r\n".encode("utf-8")
        )
        login_resp = _read_ami_until_response_reader(reader)
        if login_resp.get("Response") != AMI_SUCCESS:
            logger.warning("AMI fetch_caller_ani: Login failed %s", login_resp)
            return None
        val = _ami_dbget_channel(reader, "vikingi_ani", key)
        reader.sock.sendall(b"Action: Logoff\r\n\r\n")
        return (val or "").strip() or None
    except Exception as e:
        logger.warning("AMI fetch_caller_ani: %s", e)
        return None
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass


def fetch_incoming_did_by_session_key(hex_key: str) -> Optional[str]:
    """
    Входящий номер (DID), записанный в AstDB до AudioSocket (extensions.conf: vikingi_did/<UUID_KEY>).
    """
    key = (hex_key or "").strip().lower()
    if not key:
        return None
    try:
        from config import (
            ASTERISK_AMI_HOST,
            ASTERISK_AMI_PORT,
            ASTERISK_AMI_USER,
            ASTERISK_AMI_SECRET,
        )
    except ImportError:
        return None

    if not ASTERISK_AMI_HOST or not ASTERISK_AMI_USER:
        return None

    sock: Optional[socket.socket] = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(8.0)
        sock.connect((ASTERISK_AMI_HOST, ASTERISK_AMI_PORT))
        reader = _AmiBufferedReader(sock, timeout=8.0)
        reader.sock.sendall(
            f"Action: Login\r\n"
            f"Username: {ASTERISK_AMI_USER}\r\n"
            f"Secret: {ASTERISK_AMI_SECRET}\r\n"
            f"\r\n".encode("utf-8")
        )
        login_resp = _read_ami_until_response_reader(reader)
        if login_resp.get("Response") != AMI_SUCCESS:
            logger.warning("AMI fetch_incoming_did: Login failed %s", login_resp)
            return None
        val = _ami_dbget_channel(reader, "vikingi_did", key)
        reader.sock.sendall(b"Action: Logoff\r\n\r\n")
        return (val or "").strip() or None
    except Exception as e:
        logger.warning("AMI fetch_incoming_did: %s", e)
        return None
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass


def is_transfer_to_admin_wav(wav_file: Optional[str]) -> bool:
    """Возвращает True, если воспроизведение этого WAV означает перевод на администратора/отдел."""
    if not wav_file:
        return False
    return wav_file in (
        "03_transfer_admin.wav",
        "04_transfer_used_cars.wav",
        "05_transfer_chery_tenet.wav",
        "10_transfer_master.wav",
        "11_transfer_parts.wav",
        "13_transfer_body_repair.wav",
        "16_transfer_service_assistant.wav",
    )
