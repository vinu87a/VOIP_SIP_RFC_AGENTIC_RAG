import logging
import struct
from typing import Any, Dict, List, Optional

from .text_parser import build_message, _REQUEST_LINE, _RESPONSE_LINE

logger = logging.getLogger(__name__)

_SIP_PORTS = {5060, 5061}

_SIP_PREFIXES = (
    b"SIP/2.0",
    b"INVITE ", b"REGISTER ", b"BYE ", b"ACK ", b"CANCEL ",
    b"OPTIONS ", b"SUBSCRIBE ", b"NOTIFY ", b"PUBLISH ",
    b"MESSAGE ", b"REFER ", b"INFO ", b"UPDATE ", b"PRACK ",
)

# RTP payload type registry (RFC 3551 static assignments)
_RTP_PAYLOAD_TYPES: Dict[int, str] = {
    0:  "PCMU — G.711 μ-law (8 kHz)",
    3:  "GSM (8 kHz)",
    4:  "G.723.1 (8 kHz)",
    5:  "DVI4 (8 kHz)",
    6:  "DVI4 (16 kHz)",
    7:  "LPC (8 kHz)",
    8:  "PCMA — G.711 A-law (8 kHz)",
    9:  "G.722 (8 kHz)",
    10: "L16 stereo (44.1 kHz)",
    11: "L16 mono (44.1 kHz)",
    14: "MPA — MPEG Audio",
    15: "G.728 (8 kHz)",
    18: "G.729 (8 kHz)",
    26: "JPEG Video",
    31: "H.261 Video",
    32: "MPV — MPEG Video",
    34: "H.263 Video",
}

# Clock rates by static payload type (Hz)
_PT_CLOCK: Dict[int, int] = {
    0: 8000, 3: 8000, 4: 8000, 5: 8000, 6: 16000,
    7: 8000, 8: 8000, 9: 8000, 10: 44100, 11: 44100,
    14: 90000, 15: 8000, 18: 8000, 26: 90000, 31: 90000,
    32: 90000, 34: 90000,
}


def _looks_like_sip(payload: bytes) -> bool:
    head = payload[:20]
    return any(head.startswith(p) for p in _SIP_PREFIXES)


def _try_parse_rtp(payload: bytes) -> Optional[Dict[str, Any]]:
    """
    Attempt to interpret a UDP payload as an RTP packet (RFC 3550).
    Returns None if the payload does not look like valid RTP.
    Excludes RTCP packets (payload types 200-204).
    """
    if len(payload) < 12:
        return None

    first_byte = payload[0]
    version = (first_byte >> 6) & 0x3
    if version != 2:
        return None

    second_byte = payload[1]
    pt = second_byte & 0x7F

    # RTCP packet types overlap with RTP payload types 200-204
    if 200 <= pt <= 204:
        return None

    marker = bool((second_byte >> 7) & 0x1)
    seq    = struct.unpack("!H", payload[2:4])[0]
    ts     = struct.unpack("!I", payload[4:8])[0]
    ssrc   = struct.unpack("!I", payload[8:12])[0]

    return {"pt": pt, "marker": marker, "seq": seq, "ts": ts, "ssrc": ssrc}


def _extract_rtp_streams(packets) -> List[Dict[str, Any]]:
    """
    Walk all packets in the capture and group RTP traffic into per-SSRC streams.
    Returns one summary dict per stream — not one entry per packet — so the
    vector store stays compact even for long calls.
    """
    try:
        from scapy.all import UDP, Raw, IP, IPv6
    except ImportError:
        return []

    streams: Dict[int, Dict[str, Any]] = {}

    for pkt in packets:
        if not (pkt.haslayer(UDP) and pkt.haslayer(Raw)):
            continue

        udp = pkt[UDP]
        sport, dport = udp.sport, udp.dport

        # Skip SIP ports — we handle those separately
        if sport in _SIP_PORTS or dport in _SIP_PORTS:
            continue

        payload = bytes(pkt[Raw].load)
        rtp = _try_parse_rtp(payload)
        if rtp is None:
            continue

        ssrc = rtp["ssrc"]
        src_ip = pkt[IP].src   if pkt.haslayer(IP)   else \
                 pkt[IPv6].src if pkt.haslayer(IPv6) else ""
        dst_ip = pkt[IP].dst   if pkt.haslayer(IP)   else \
                 pkt[IPv6].dst if pkt.haslayer(IPv6) else ""

        if ssrc not in streams:
            streams[ssrc] = {
                "ssrc": ssrc,
                "pt": rtp["pt"],
                "src_ip": src_ip,
                "dst_ip": dst_ip,
                "src_port": sport,
                "dst_port": dport,
                "first_seq": rtp["seq"],
                "last_seq":  rtp["seq"],
                "first_ts":  rtp["ts"],
                "last_ts":   rtp["ts"],
                "pkt_count": 1,
                "marker_count": 1 if rtp["marker"] else 0,
            }
        else:
            s = streams[ssrc]
            s["last_seq"]  = rtp["seq"]
            s["last_ts"]   = rtp["ts"]
            s["pkt_count"] += 1
            if rtp["marker"]:
                s["marker_count"] += 1

    # Convert each stream into a text-searchable message dict
    result: List[Dict[str, Any]] = []
    for ssrc, s in streams.items():
        pt       = s["pt"]
        pt_label = _RTP_PAYLOAD_TYPES.get(pt, f"Dynamic PT {pt}" if pt >= 96 else f"Unknown PT {pt}")
        clock    = _PT_CLOCK.get(pt, 8000)

        # Timestamp difference — handle 32-bit wrap-around
        ts_diff  = (s["last_ts"] - s["first_ts"]) & 0xFFFFFFFF
        duration = ts_diff / clock if ts_diff > 0 else 0.0

        summary = (
            f"RTP Stream SSRC=0x{ssrc:08X}:\n"
            f"  Payload Type : {pt} ({pt_label})\n"
            f"  Direction    : {s['src_ip']}:{s['src_port']} → {s['dst_ip']}:{s['dst_port']}\n"
            f"  Packets      : {s['pkt_count']}\n"
            f"  Seq range    : {s['first_seq']} → {s['last_seq']}\n"
            f"  Timestamp    : {s['first_ts']} → {s['last_ts']} (clock {clock} Hz)\n"
            f"  Duration     : ~{duration:.2f} seconds\n"
            f"  Marker bits  : {s['marker_count']} (talk-spurt boundaries)\n"
        )

        result.append({
            "raw":           summary,
            "type":          "rtp_stream",
            "method":        "RTP",
            "response_code": 0,
            "call_id":       "",
            "cseq":          "",
            "src_ip":        s["src_ip"],
            "dst_ip":        s["dst_ip"],
            "src_port":      s["src_port"],
            "dst_port":      s["dst_port"],
        })

    logger.info(f"PCAP parser: found {len(streams)} RTP stream(s)")
    return result


def parse_pcap_trace(file_path: str) -> List[Dict[str, Any]]:
    """
    Extract SIP messages AND RTP stream summaries from a PCAP/PCAPNG file.
    SIP entries contain the full raw message text.
    RTP entries contain a human-readable stream summary (one per SSRC).
    """
    try:
        from scapy.all import rdpcap, UDP, TCP, Raw, IP, IPv6
        from scapy.error import Scapy_Exception
    except ImportError:
        raise RuntimeError(
            "scapy is required for PCAP parsing. Install it with: pip install scapy"
        )

    try:
        packets = rdpcap(file_path)
    except Scapy_Exception as exc:
        raise RuntimeError(f"Cannot read PCAP file '{file_path}': {exc}")

    messages: List[Dict[str, Any]] = []

    # ── SIP messages ──────────────────────────────────────────────────────────
    for pkt in packets:
        if not pkt.haslayer(Raw):
            continue

        if pkt.haslayer(UDP):
            transport = pkt[UDP]
        elif pkt.haslayer(TCP):
            transport = pkt[TCP]
        else:
            continue

        payload: bytes = pkt[Raw].load
        sport, dport   = transport.sport, transport.dport

        if sport not in _SIP_PORTS and dport not in _SIP_PORTS:
            if not _looks_like_sip(payload):
                continue

        try:
            text = payload.decode("utf-8", errors="ignore").strip()
            if not text:
                continue
            first_line = text.splitlines()[0]
            if not (_REQUEST_LINE.match(first_line) or _RESPONSE_LINE.match(first_line)):
                continue

            msg = build_message(text)
            if pkt.haslayer(IP):
                msg["src_ip"] = pkt[IP].src
                msg["dst_ip"] = pkt[IP].dst
            elif pkt.haslayer(IPv6):
                msg["src_ip"] = pkt[IPv6].src
                msg["dst_ip"] = pkt[IPv6].dst
            msg["src_port"] = sport
            msg["dst_port"] = dport
            messages.append(msg)
        except Exception as exc:
            logger.debug(f"Skipping SIP packet: {exc}")

    sip_count = len(messages)

    # ── RTP stream summaries ──────────────────────────────────────────────────
    rtp_entries = _extract_rtp_streams(packets)
    messages.extend(rtp_entries)

    logger.info(
        f"PCAP parser: {sip_count} SIP messages + "
        f"{len(rtp_entries)} RTP stream(s) in {len(packets)} packets"
    )
    return messages
