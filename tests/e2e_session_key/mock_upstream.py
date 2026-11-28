"""Mock OpenAI-compatible upstream. Path: /e/<lane>/<idx>/v1/chat/completions.
Markers in conversation text (never secrets) drive behaviour:
  MOCK_STATUS::<code>::<lane>   every entry in <lane> returns <code>
  MOCK_429_FIRST                 the first entry hit for this conversation returns 429
  MOCK_STALL::<lane>             streaming: entries in <lane> stall before first chunk
  USE_TOOL                       return a tool call for the first tool
Every request is appended to UPLOG as {entry_idx, lane, stream, canaries:[names]}
(canary NAMES only, matched from a canary list file; values never written)."""
import json, sys, time, hashlib, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
PORT=int(sys.argv[1]); UPLOG=sys.argv[2]; CANARY_FILE=sys.argv[3]
LOCK=threading.Lock(); FIRST_SEEN=set()
def canaries():
    try: return json.load(open(CANARY_FILE))
    except Exception: return {}
def text_of(body):
    out=[]
    for m in body.get("messages") or []:
        c=m.get("content")
        if isinstance(c,str): out.append(c)
        elif isinstance(c,list): out += [p.get("text","") for p in c if isinstance(p,dict)]
    return "\n".join(out)
def completion(model, content, tool_calls=None, finish="stop"):
    msg={"role":"assistant","content":content}
    if tool_calls: msg["tool_calls"]=tool_calls
    return {"id":"x","object":"chat.completion","model":model,"choices":[{"index":0,"message":msg,"finish_reason":finish}],
            "usage":{"prompt_tokens":100,"completion_tokens":10,"total_tokens":110}}
def chunk(model, delta, finish=None):
    return {"id":"x","object":"chat.completion.chunk","model":model,"choices":[{"index":0,"delta":delta,"finish_reason":finish}]}
class H(BaseHTTPRequestHandler):
    protocol_version="HTTP/1.1"
    def log_message(self,*a): pass
    def do_POST(self):
        parts=self.path.split("/")  # '', 'e', lane, idx, 'v1', 'chat', 'completions'
        lane, idx = parts[2], int(parts[3])
        body=json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        model=body.get("model"); text=text_of(body)+"\n"+json.dumps(body.get("messages"))
        hit=[n for n,v in canaries().items() if v in text]
        with LOCK:
            open(UPLOG,"a").write(json.dumps({"idx":idx,"lane":lane,"stream":bool(body.get("stream")),"canaries":hit})+"\n")
        for code in (402,403,429,500,401):
            if f"MOCK_STATUS::{code}::{lane}" in text:
                return self._send(code, {"error":{"message":f"mock {code}"}})
        if "MOCK_429_FIRST" in text:
            k=hashlib.sha1(text.encode()).hexdigest()
            with LOCK:
                first = k not in FIRST_SEEN; FIRST_SEEN.add(k)
            if first: return self._send(429, {"error":{"message":"mock quota"}})
        tools=body.get("tools") or []
        want_tool = tools and "USE_TOOL" in text and not any(m.get("role")=="tool" for m in body.get("messages") or [])
        if body.get("stream"):
            if f"MOCK_STALL::{lane}" in text: time.sleep(6)
            if want_tool:
                name=tools[0]["function"]["name"]
                ch=[chunk(model,{"role":"assistant","tool_calls":[{"index":0,"id":"c1","type":"function","function":{"name":name,"arguments":""}}]}),
                    chunk(model,{"tool_calls":[{"index":0,"function":{"arguments":'{"command": '}}]}),
                    chunk(model,{"tool_calls":[{"index":0,"function":{"arguments":'"ls"}'}}]}),
                    chunk(model,{},finish="tool_calls")]
            else:
                ch=[chunk(model,{"role":"assistant","content":"Hello "}),chunk(model,{"content":"world, ünïcødé ✓"}),chunk(model,{},finish="stop")]
            data="".join(f"data: {json.dumps(c)}\n\n" for c in ch)
            data+=f"data: {json.dumps({'id':'x','choices':[],'usage':{'prompt_tokens':100,'completion_tokens':6,'total_tokens':106}})}\n\n"+"data: [DONE]\n\n"
            return self._raw(200,data.encode(),"text/event-stream")
        if want_tool:
            name=tools[0]["function"]["name"]
            return self._send(200, completion(model,None,tool_calls=[{"id":"c1","type":"function","function":{"name":name,"arguments":'{"command":"ls"}'}}],finish="tool_calls"))
        return self._send(200, completion(model, f"answer from {model}: done. Status nominal."))
    def _send(self,code,obj): self._raw(code,json.dumps(obj).encode(),"application/json")
    def _raw(self,code,data,ctype):
        self.send_response(code); self.send_header("Content-Type",ctype); self.send_header("Content-Length",str(len(data))); self.end_headers(); self.wfile.write(data)
ThreadingHTTPServer(("127.0.0.1",PORT),H).serve_forever()
