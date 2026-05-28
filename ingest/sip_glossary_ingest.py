"""
Ingests the SIP/VoIP terminology glossary into the RFC knowledge base.

Covers ~167 terms across 10 categories:
  SIP Methods, SIP Response Codes, SIP Headers, SDP & Media,
  Network Components, Addressing & Routing, Call Flow & State,
  Security & Transport, QoS & Performance, Protocols & Standards.

Each term is indexed with its short form (acronym), full form, and definition
so both keyword and semantic search work reliably for abbreviations.
"""

import csv
import io
import logging
import uuid
from typing import Dict, List

logger = logging.getLogger(__name__)

DOC_ID    = "SIP-GLOSSARY"
DOC_TITLE = "SIP/VoIP Terminology Glossary"

# ---------------------------------------------------------------------------
# Raw glossary data (CSV embedded for self-containment)
# Columns: Category, Term, Acronym, Full Form, Definition
# ---------------------------------------------------------------------------
_CSV_DATA = """\
Category,Term,Acronym,Full Form,Definition
SIP Methods,INVITE,,,"Initiates a session or invites a user to participate in a call. The primary method to set up a voice/video call."
SIP Methods,ACK,,,"Acknowledges the final response to an INVITE. Completes the 3-way SIP handshake (INVITE → 200 OK → ACK)."
SIP Methods,BYE,,,"Terminates an established session. Either party can send BYE to end the call."
SIP Methods,CANCEL,,,"Cancels a pending INVITE before a final response is received. Used when the caller hangs up before the callee answers."
SIP Methods,REGISTER,,,"Registers a SIP URI and its current contact address (IP:port) with a SIP registrar server."
SIP Methods,OPTIONS,,,"Queries the capabilities of a SIP endpoint or server. Often used as a keepalive / SIP ping mechanism."
SIP Methods,REFER,,,"Requests the recipient to contact a third party — the basis for call transfer (attended and blind)."
SIP Methods,SUBSCRIBE,,,"Requests notification of events at a remote node, e.g. presence state or message waiting indication (MWI)."
SIP Methods,NOTIFY,,,"Sends information about an event to a subscriber. Always paired with a SUBSCRIBE."
SIP Methods,PUBLISH,,,"Publishes event state to an event state compositor, e.g. for presence (online/busy/away)."
SIP Methods,MESSAGE,,,"Carries instant message content in the SIP body. Defined in RFC 3428."
SIP Methods,UPDATE,,,"Modifies the session state (codec, hold) before the session is confirmed, without completing a re-INVITE."
SIP Methods,INFO,,,"Sends mid-call signaling (e.g. DTMF via RFC 2976, FAX T.38 negotiation) within an existing dialog."
SIP Methods,PRACK,PRACK,Provisional Response ACKnowledgement,"Acknowledges 1xx provisional responses when reliable provisional responses (100rel) are used."
SIP Response Codes,100 Trying,,,"Indicates the next-hop server has received the request and is processing it. Not forwarded end-to-end."
SIP Response Codes,180 Ringing,,,"The destination user agent is alerting (phone is ringing). Triggers ringback tone on the caller side."
SIP Response Codes,181 Call Being Forwarded,,,"Informs the caller that the call is being forwarded to another destination."
SIP Response Codes,183 Session Progress,,,"Carries early media (ringback from remote PSTN) before the call is answered. Contains SDP."
SIP Response Codes,200 OK,,,"Request was successful. In response to INVITE, carries the answerer's SDP for media negotiation."
SIP Response Codes,202 Accepted,,,"Request accepted for processing but not completed. Used with REFER and SUBSCRIBE."
SIP Response Codes,301 Moved Permanently,,,"The user can no longer be reached at this URI. New URI provided in Contact header."
SIP Response Codes,302 Moved Temporarily,,,"The user is temporarily at another URI. Common in call forwarding scenarios."
SIP Response Codes,400 Bad Request,,,"Malformed request syntax or invalid message framing."
SIP Response Codes,401 Unauthorized,,,"Requires authentication. Used by UAS/registrar to challenge the UAC for credentials (Digest auth)."
SIP Response Codes,403 Forbidden,,,"Server understood the request but refuses to process it. Credentials rejected."
SIP Response Codes,404 Not Found,,,"The user identified in the Request-URI does not exist at the domain."
SIP Response Codes,407 Proxy Auth Required,,,"The proxy requires authentication. Similar to 401 but issued by a proxy server."
SIP Response Codes,408 Request Timeout,,,"The server could not produce a response within a suitable time."
SIP Response Codes,480 Temporarily Unavailable,,,"Callee exists but is currently unavailable (e.g. not registered, DND)."
SIP Response Codes,481 Call/Transaction Does Not Exist,,,"Server received a BYE or CANCEL but the dialog/transaction is unknown."
SIP Response Codes,486 Busy Here,,,"Callee's endpoint is busy. The call may succeed elsewhere."
SIP Response Codes,487 Request Terminated,,,"Sent in response to a CANCEL — the INVITE was successfully cancelled."
SIP Response Codes,488 Not Acceptable Here,,,"Media negotiation failed — the offered SDP codecs/parameters are not acceptable."
SIP Response Codes,491 Request Pending,,,"Sent when a re-INVITE arrives while a previous transaction is in progress."
SIP Response Codes,500 Server Internal Error,,,"Generic server-side failure."
SIP Response Codes,503 Service Unavailable,,,"Server temporarily unable to process the request. SBC/gateway overloaded or down."
SIP Response Codes,504 Server Timeout,,,"The server did not receive a timely response from an upstream server it tried to contact."
SIP Headers,Via,,,"Records the path a request has taken. Each proxy adds its own Via; responses follow this path back."
SIP Headers,From,,,"Logical identity of the request initiator (calling party). May differ from the network address."
SIP Headers,To,,,"Logical identity of the request recipient (called party). Identifies the target of the dialog."
SIP Headers,Call-ID,,,"Globally unique identifier for a SIP call/session. Combined with From and To tags to identify a dialog."
SIP Headers,CSeq,CSeq,Command Sequence,"Contains a sequence number and method name. Orders transactions within a dialog."
SIP Headers,Contact,,,"Provides a direct URI for future requests in a dialog. Points to the actual UA, bypassing proxies."
SIP Headers,Max-Forwards,,,"Limits the number of hops a request can traverse. Decremented by each proxy (starts at 70)."
SIP Headers,Record-Route,,,"Inserted by proxies to stay in the signaling path of subsequent requests within the dialog."
SIP Headers,Route,,,"Tells a UAC which proxies to traverse, built from Record-Route headers of prior responses."
SIP Headers,Content-Type,,,"Describes the body type, most commonly application/sdp for media negotiation."
SIP Headers,Content-Length,,,"Byte count of the message body. Mandatory when a body is present."
SIP Headers,P-Asserted-Identity,PAI,P-Asserted-Identity,"Contains the verified caller identity asserted by a trusted network element (RFC 3325). Used for CLID."
SIP Headers,Remote-Party-ID,RPID,Remote-Party-ID,"Cisco/older header carrying caller identity information. Functionally similar to PAI."
SIP Headers,Diversion,,,"Indicates that the call was diverted/forwarded, carrying the original and diverting addresses."
SIP Headers,Referred-By,,,"Identifies the party that issued the REFER (used in call transfer scenarios)."
SIP Headers,Replaces,,,"Identifies a dialog to be replaced — used in attended transfers to splice in the transferor."
SIP Headers,X-MS-SBC,,,"Microsoft-specific header used in Direct Routing signaling between Teams and the SBC."
SIP Headers,Session-Expires,,,"Negotiated session timer value in seconds. Triggers re-INVITE or UPDATE keep-alive before expiry."
SIP Headers,Min-SE,,,"Minimum Session-Expires value the UAS will accept. Causes 422 rejection if INVITE offers less."
SDP & Media,SDP,SDP,Session Description Protocol,"Describes media sessions: codecs, IP, ports, transport (RFC 4566). Carried as the body of INVITE/200 OK."
SDP & Media,RTP,RTP,Real-time Transport Protocol,"Carries the actual audio/video media streams over UDP. Defined in RFC 3550."
SDP & Media,RTCP,RTCP,RTP Control Protocol,"Companion to RTP, carries QoS statistics (packet loss, jitter, delay) between endpoints."
SDP & Media,SRTP,SRTP,Secure Real-time Transport Protocol,"Adds encryption and message authentication to RTP media streams (RFC 3711)."
SDP & Media,DTLS,DTLS,Datagram Transport Layer Security,"UDP-based TLS. Used in DTLS-SRTP to exchange SRTP keys. Mandatory in WebRTC."
SDP & Media,DTMF,DTMF,Dual-Tone Multi-Frequency,"Touch-tone signaling. Carried in-band (G.711) or out-of-band via RFC 4733 (telephone-event) RTP payloads or SIP INFO."
SDP & Media,ICE,ICE,Interactive Connectivity Establishment,"NAT traversal framework using candidate gathering (host, srflx, relay). RFC 8445."
SDP & Media,STUN,STUN,Session Traversal Utilities for NAT,"Discovers a device's public IP/port and checks connectivity. Used in ICE."
SDP & Media,TURN,TURN,Traversal Using Relays around NAT,"Relays media when direct or STUN paths are blocked by symmetric NAT."
SDP & Media,SSRC,SSRC,Synchronization Source,"32-bit identifier for an RTP stream within a session."
SDP & Media,MOS,MOS,Mean Opinion Score,"Subjective voice quality metric on a 1–5 scale. MOS >= 4.0 is considered good quality."
SDP & Media,Ptime,ptime,Packetization Time,"Duration of audio per RTP packet (e.g. 20 ms). Affects latency and packet rate."
SDP & Media,Offer/Answer Model,,,"Two-step SDP exchange: caller offers media parameters in INVITE, callee answers in 200 OK (RFC 3264)."
SDP & Media,G.711,,,"PCM codec at 64 kbps: mu-law (PCMU, North America) and A-law (PCMA, Europe). Highest quality, highest bandwidth."
SDP & Media,G.729,,,"Compressed codec at 8 kbps. Popular for WAN/trunk efficiency. Licensed."
SDP & Media,G.722,,,"Wideband (HD voice) codec at 64 kbps, 7 kHz audio. Used in Microsoft Teams and Cisco CUCM."
SDP & Media,Opus,,,"Open royalty-free codec used in WebRTC. Adaptive bitrate (6-510 kbps), wideband and fullband."
SDP & Media,T.38,,,"ITU standard for fax over IP (FoIP). Negotiated via SIP re-INVITE to switch from audio to T.38 mode."
SDP & Media,Jitter Buffer,,,"Buffers incoming RTP packets to compensate for network jitter and reorder out-of-sequence packets."
Network Components,UAC,UAC,User Agent Client,"The SIP entity that initiates requests (e.g. the calling device or application)."
Network Components,UAS,UAS,User Agent Server,"The SIP entity that receives and responds to requests (e.g. the called device)."
Network Components,UA,UA,User Agent,"Generic SIP endpoint acting as both UAC and UAS (e.g. softphone, IP phone)."
Network Components,B2BUA,B2BUA,Back-to-Back User Agent,"Acts as UAS to one side and UAC to the other. Full call control. SBCs are B2BUAs."
Network Components,SBC,SBC,Session Border Controller,"Sits at network boundaries. Provides NAT traversal, security (topology hiding, DoS protection), transcoding, and policy enforcement."
Network Components,PBX,PBX,Private Branch Exchange,"On-prem telephony system. Modern IP PBXs (Cisco CUCM, Avaya CM, Asterisk) use SIP or H.323."
Network Components,PSTN,PSTN,Public Switched Telephone Network,"Traditional circuit-switched telephone network. Connected to VoIP via gateways or SIP trunks."
Network Components,ITSP,ITSP,Internet Telephony Service Provider,"Provides SIP trunking, DID numbers, and PSTN interconnect."
Network Components,UCaaS,UCaaS,Unified Communications as a Service,"Cloud-delivered UC platform (e.g. Microsoft Teams, Webex, Zoom Phone)."
Network Components,IMS,IMS,IP Multimedia Subsystem,"3GPP architectural framework for delivering IP multimedia services over mobile and fixed networks using SIP."
Network Components,CSCF,CSCF,Call Session Control Function,"Core SIP proxy in an IMS network. Three variants: P-CSCF (proxy), I-CSCF (interrogating), S-CSCF (serving)."
Network Components,Proxy Server,,,"Routes SIP requests on behalf of other clients. Stateful proxies maintain transaction state."
Network Components,Registrar,,,"A SIP server that accepts REGISTER requests and maintains a location database mapping AOR to contact URIs."
Network Components,Redirect Server,,,"Returns 3xx responses directing the UAC to contact an alternate URI, without forwarding the request itself."
Network Components,SIP Trunk,,,"Virtual connection between a PBX/UCaaS platform and an ITSP over IP, replacing legacy PRI/ISDN circuits."
Network Components,Media Gateway,,,"Converts between IP media (RTP) and TDM/PSTN media (T1/E1, analog). Controlled by MGCP or H.248."
Network Components,WebRTC,WebRTC,Web Real-Time Communication,"Browser API for peer-to-peer voice/video using ICE, DTLS-SRTP, and SCTP. Uses SDP offer/answer."
Addressing & Routing,SIP URI,URI,Uniform Resource Identifier,"SIP address in the form sip:user@domain. The fundamental addressing scheme in SIP."
Addressing & Routing,SIPS URI,,,"Secure SIP URI (sips:user@domain) — mandates TLS transport for the entire signaling path."
Addressing & Routing,TEL URI,,,"tel:+14085551234 — E.164 phone number URI. Often mapped to a SIP URI for routing."
Addressing & Routing,AOR,AOR,Address of Record,"A SIP URI representing a user's canonical identity. May map to multiple contacts."
Addressing & Routing,E.164,,,"ITU numbering standard: +[country code][subscriber number], max 15 digits."
Addressing & Routing,DID,DID,Direct Inward Dialing,"Phone numbers that route directly to a specific extension without attendant intervention."
Addressing & Routing,DDI,DDI,Direct Dial-In,"UK/European equivalent of DID."
Addressing & Routing,DNS SRV,SRV,Service Record,"DNS record used to discover SIP servers: _sip._tcp or _sip._udp. Returns priority/weight/host/port."
Addressing & Routing,NAPTR,NAPTR,Naming Authority Pointer,"DNS record used in ENUM to map E.164 numbers to SIP URIs, and in SIP server discovery."
Addressing & Routing,ENUM,ENUM,Electronic Numbering,"Uses DNS to map E.164 telephone numbers to URIs (RFC 6116). Used for on-net routing."
Addressing & Routing,FQDN,FQDN,Fully Qualified Domain Name,"SBCs and carriers verify TLS certificates against FQDNs rather than IPs."
Addressing & Routing,Trunk Group,,,"Logical grouping of SIP channels/trunks for capacity and routing management."
Addressing & Routing,Dial Plan,,,"Rules that determine how dialed digits are interpreted and routed. Pattern matching maps digits to destinations."
Addressing & Routing,Normalization,,,"Transforming phone numbers to a canonical format (usually E.164) for consistent routing and CLID presentation."
Addressing & Routing,CLID,CLID,Calling Line Identification,"The caller's number or identity presented to the called party. Also called CLI or Caller ID."
Addressing & Routing,ANI,ANI,Automatic Number Identification,"The billing number of the calling party. Similar to CLID but used in carrier/SS7 context."
Addressing & Routing,DNIS,DNIS,Dialed Number Identification Service,"Identifies the number the caller dialed. Used in IVR/contact center routing."
Call Flow & State,Dialog,,,"A peer-to-peer SIP relationship between two UAs identified by Call-ID + From-tag + To-tag. Persists for the call duration."
Call Flow & State,Transaction,,,"A single request and its responses. INVITE transactions include INVITE + provisional + final response."
Call Flow & State,Session,,,"The actual media exchange (RTP streams). A dialog can have zero or more associated sessions."
Call Flow & State,Early Dialog,,,"A dialog established by a provisional (1xx) response before the final response. Carries early media (183)."
Call Flow & State,Re-INVITE,,,"An INVITE within an existing dialog to modify session parameters: hold, codec change, add video."
Call Flow & State,Hold,,,"Suspends media using sendonly/inactive SDP direction attributes or 0.0.0.0 connection address."
Call Flow & State,Transfer (Blind),,,"Transferring a call to a third party without consulting them first. Uses REFER with a Refer-To header."
Call Flow & State,Transfer (Attended),,,"Calling the transfer target first (consultation), then transferring using REFER with a Replaces header."
Call Flow & State,Call Park,,,"Placing a call in a shared hold location (orbit/slot) retrievable from any extension."
Call Flow & State,Call Pickup,,,"Answering a ringing call on behalf of another extension via SUBSCRIBE/NOTIFY or INVITE with Replaces."
Call Flow & State,Forking,,,"A proxy sending an INVITE to multiple contacts simultaneously (parallel) or in sequence (sequential)."
Call Flow & State,Presence,,,"Real-time availability state (available, busy, away, DND) published/subscribed via SIP PUBLISH/SUBSCRIBE/NOTIFY."
Call Flow & State,MWI,MWI,Message Waiting Indication,"Lamp/notification on IP phones indicating voicemail. Delivered via SUBSCRIBE/NOTIFY."
Call Flow & State,CFU,CFU,Call Forward Unconditional,"Forwards all incoming calls to another number regardless of status."
Call Flow & State,CFB,CFB,Call Forward on Busy,"Forwards calls only when the endpoint is busy."
Call Flow & State,CFNA,CFNA,Call Forward No Answer,"Forwards calls when the endpoint does not answer within a timeout."
Call Flow & State,IVR,IVR,Interactive Voice Response,"Automated system that interacts with callers via voice prompts and DTMF input for routing/self-service."
Call Flow & State,ACD,ACD,Automatic Call Distribution,"Routes incoming calls to the most appropriate agent or queue in a contact center."
Call Flow & State,CDR,CDR,Call Detail Record,"Log entry capturing call metadata: parties, timestamps, duration, disposition, SIP response codes."
Security & Transport,TLS,TLS,Transport Layer Security,"Encrypts SIP signaling. Uses TCP port 5061. Required for SIPS URIs and Microsoft Direct Routing."
Security & Transport,TCP,TCP,Transmission Control Protocol,"Reliable SIP transport. Used when messages exceed UDP MTU or when TLS is required."
Security & Transport,UDP,UDP,User Datagram Protocol,"Default SIP transport on port 5060. Faster but unreliable; SIP handles retransmission via T1/T2 timers."
Security & Transport,mTLS,mTLS,Mutual TLS,"Both client and server present certificates. Required by Microsoft Teams Direct Routing for SBC authentication."
Security & Transport,SNI,SNI,Server Name Indication,"TLS extension allowing a server to present multiple certificates on one IP. Used in SBC/Teams scenarios."
Security & Transport,Digest Authentication,,,"SIP's challenge-response auth (RFC 3261). Server sends 401/407 with nonce; client hashes credentials and resubmits."
Security & Transport,IP ACL,ACL,Access Control List,"Permits/denies SIP traffic by source IP. First line of SBC defense against toll fraud."
Security & Transport,DoS / DDoS,DoS/DDoS,Denial of Service / Distributed Denial of Service,"SBC rate-limits and filters abusive SIP traffic (INVITE floods, OPTIONS floods, registration attacks)."
Security & Transport,SPIT,SPIT,Spam over Internet Telephony,"Unsolicited bulk VoIP calls. Analogue of email spam."
Security & Transport,Toll Fraud,,,"Unauthorized use of a VoIP system to make expensive calls via exposed SIP ports and weak credentials."
Security & Transport,Topology Hiding,,,"SBC function that strips internal Via/Record-Route headers before forwarding, hiding the internal network."
Security & Transport,SRST,SRST,Survivable Remote Site Telephony,"Cisco feature allowing a branch router to provide basic call processing if WAN connectivity to CUCM is lost."
Security & Transport,PKI,PKI,Public Key Infrastructure,"Framework of certificates, CAs, and key management used to authenticate SIP TLS connections."
QoS & Performance,QoS,QoS,Quality of Service,"Mechanisms (DSCP marking, traffic shaping, priority queuing) to protect real-time voice/video traffic."
QoS & Performance,DSCP,DSCP,Differentiated Services Code Point,"6-bit IP header field. Voice RTP: EF (46/0x2E); SIP signaling: AF31 or CS3."
QoS & Performance,EF,EF,Expedited Forwarding,"DSCP value 46 — highest priority PHB, used for VoIP RTP media to minimize latency and jitter."
QoS & Performance,AF,AF,Assured Forwarding,"DSCP class with guaranteed bandwidth and drop precedence. AF31 commonly used for SIP signaling."
QoS & Performance,Jitter,,,"Variation in packet arrival times. Excessive jitter causes choppy audio, absorbed by the jitter buffer."
QoS & Performance,MOS,MOS,Mean Opinion Score,"Subjective voice quality metric (1-5). MOS >= 4.0 considered good. Estimated by E-Model (R-factor)."
QoS & Performance,E-Model,,,"ITU-T G.107 algorithm computing an R-factor (0-100) from delay, jitter, loss, and codec quality. Converts to estimated MOS."
QoS & Performance,PLC,PLC,Packet Loss Concealment,"Codec technique that interpolates missing audio to mask lost RTP packets."
QoS & Performance,VAD,VAD,Voice Activity Detection,"Suppresses transmission during silence, reducing bandwidth. Can cause clipping if overly aggressive."
QoS & Performance,CNG,CNG,Comfort Noise Generation,"Injects synthetic background noise during VAD silence to avoid dead air."
QoS & Performance,AEC,AEC,Acoustic Echo Cancellation,"Removes echo caused by speaker-to-microphone coupling in full-duplex calls."
QoS & Performance,Transcoding,,,"Converting media from one codec to another in real-time (e.g. G.729 to G.711). Performed by SBCs or gateways."
QoS & Performance,MTU,MTU,Maximum Transmission Unit,"Largest packet size allowed on a network path. SIP messages exceeding ~1300 bytes may need TCP instead of UDP."
QoS & Performance,RTT,RTT,Round-Trip Time,"Time for a packet to travel from source to destination and back. Used in RTCP and network troubleshooting."
Protocols & Standards,SIP,SIP,Session Initiation Protocol,"IETF application-layer signaling protocol (RFC 3261) for creating, modifying, and terminating multimedia sessions."
Protocols & Standards,VoIP,VoIP,Voice over Internet Protocol,"Technology for delivering voice communications over IP networks instead of traditional circuit-switched PSTN."
Protocols & Standards,H.323,,,"ITU umbrella standard for multimedia over packet networks. Predates SIP; still used in videoconferencing."
Protocols & Standards,MGCP,MGCP,Media Gateway Control Protocol,"Centralized control of media gateways by a call agent. Mostly legacy, superseded by H.248/Megaco."
Protocols & Standards,H.248 / Megaco,Megaco,Media Gateway Control,"Successor to MGCP for gateway control. IETF RFC 3525 / ITU H.248."
Protocols & Standards,XMPP,XMPP,Extensible Messaging and Presence Protocol,"Used for presence and IM in some UC systems (Cisco Jabber). Can gateway to SIP."
Protocols & Standards,WebSocket,,,"Full-duplex TCP connection used to carry SIP for browser-based clients (SIP over WebSocket, RFC 7118)."
Protocols & Standards,SIGTRAN,SIGTRAN,Signaling Transport,"SS7 over IP — carries PSTN signaling (ISUP, SCCP) over IP using SCTP. Used in carrier interconnects."
Protocols & Standards,SS7,SS7,Signaling System No. 7,"PSTN signaling protocol stack (MTP, SCCP, TCAP, ISUP). Used for PSTN call control and SMS."
Protocols & Standards,SCTP,SCTP,Stream Control Transmission Protocol,"Transport protocol used by SIGTRAN. Provides multi-streaming and multi-homing over IP."
Protocols & Standards,ISUP,ISUP,ISDN User Part,"SS7 protocol layer for call control on PSTN. SIP-T/SIP-I encapsulates ISUP in SIP for interworking."
Protocols & Standards,SIP-I / SIP-T,,,"SIP with ISUP — encapsulates SS7 ISUP messages in SIP bodies for PSTN interworking."
Protocols & Standards,RFC 3261,,,"The core SIP specification. Supersedes RFC 2543. Defines methods, headers, dialog, and transaction behavior."
Protocols & Standards,RFC 4566,,,"SDP — Session Description Protocol specification."
Protocols & Standards,RFC 3550,,,"RTP/RTCP specification."
Protocols & Standards,RFC 4733,,,"RTP Payload for DTMF Digits (telephone-event). Successor to RFC 2833."
Protocols & Standards,3GPP,3GPP,3rd Generation Partnership Project,"Standards body defining mobile network specs including IMS, VoLTE, and SIP usage in LTE/5G."
Protocols & Standards,VoLTE,VoLTE,Voice over LTE,"Voice calls carried over 4G LTE using IMS/SIP instead of circuit-switched fallback."
Protocols & Standards,WebRTC,WebRTC,Web Real-Time Communication,"W3C/IETF standard enabling browser-based real-time voice/video using SDP, ICE, DTLS-SRTP."
"""


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _parse_csv() -> List[Dict[str, str]]:
    """Return list of dicts with keys: category, term, acronym, full_form, definition."""
    reader = csv.DictReader(io.StringIO(_CSV_DATA.strip()))
    rows = []
    for row in reader:
        rows.append({
            "category":   row["Category"].strip(),
            "term":       row["Term"].strip(),
            "acronym":    row["Acronym"].strip(),
            "full_form":  row["Full Form"].strip(),
            "definition": row["Definition"].strip(),
        })
    return rows


def _format_entry(row: Dict[str, str]) -> str:
    """
    Format one glossary entry as a single readable text block.
    Includes both short form (acronym) and full form so keyword
    and semantic search both work on the same chunk.

    Example output:
      PRACK (Provisional Response ACKnowledgement):
        Acknowledges 1xx provisional responses when reliable provisional
        responses (100rel) are used.
    """
    term  = row["term"]
    acr   = row["acronym"]
    full  = row["full_form"]
    defn  = row["definition"]

    if acr and full:
        header = f"{term} ({acr} — {full})"
    elif acr:
        header = f"{term} ({acr})"
    elif full:
        header = f"{term} ({full})"
    else:
        header = term

    return f"{header}:\n  {defn}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fetch_sip_glossary_chunks(chunk_size: int = 1800) -> List[Dict]:
    """
    Parse the embedded glossary CSV and return chunks ready for ChromaDB upsert.

    Chunking strategy: group entries by category, then split within a category
    if the accumulated text exceeds chunk_size characters.
    """
    rows = _parse_csv()

    # Group entries by category preserving insertion order
    by_category: Dict[str, List[str]] = {}
    for row in rows:
        entry = _format_entry(row)
        by_category.setdefault(row["category"], []).append(entry)

    chunks: List[Dict] = []

    for category, entries in by_category.items():
        header     = f"SIP/VoIP Glossary — {category}\n\n"
        current    = header
        chunk_idx  = 0

        for entry in entries:
            block     = entry + "\n\n"
            candidate = current + block
            if len(candidate) > chunk_size and len(current) > len(header):
                chunks.append(_make_chunk(current.strip(), category, chunk_idx))
                chunk_idx += 1
                current = header + block
            else:
                current = candidate

        if current.strip() and current.strip() != header.strip():
            chunks.append(_make_chunk(current.strip(), category, chunk_idx))

    logger.info(
        "SIP Glossary ingest: %d terms across %d categories → %d chunks",
        len(rows), len(by_category), len(chunks),
    )
    return chunks


def _make_chunk(text: str, category: str, idx: int) -> Dict:
    return {
        "id":            f"{DOC_ID}_{category[:12].replace(' ', '_')}_{idx}_{uuid.uuid4().hex[:6]}",
        "text":          text,
        "rfc_no":        DOC_ID,
        "rfc_title":     DOC_TITLE,
        "section_no":    str(idx),
        "section_title": category,
        "chunk_idx":     idx,
    }
