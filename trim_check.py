import struct
from pathlib import Path

src = Path("data/uploads/20260815T135853_d207e2f63efa45c3a9f725fbf146928e.wav")
data = src.read_bytes()
i = 12
data_off = data_size = 0
while i + 8 <= len(data):
    cid = data[i:i+4]
    csz = struct.unpack("<I", data[i+4:i+8])[0]
    if cid == b"data":
        data_off = i + 8
        data_size = csz
        break
    i += 8 + csz
samples = list(struct.unpack(f"<{data_size//2}h", data[data_off:data_off+data_size]))

DC = 1927
first_real = next(k for k, s in enumerate(samples) if abs(s - DC) > 100)
last_real = len(samples) - next(k for k, s in enumerate(reversed(samples)) if abs(s - DC) > 100)
kept = samples[first_real:last_real]
print(f"kept {len(kept)} samples = {len(kept)/22050:.2f}s")

# Rewrite WAV header + new data size
def wav_bytes(samples, sr=22050, ch=1, bits=16):
    import io
    byte_rate = sr * ch * bits // 8
    block_align = ch * bits // 8
    data_size = len(samples) * bits // 8
    fmt = struct.pack("<HHIIHH", 1, ch, sr, byte_rate, block_align, bits)
    header = b"RIFF" + struct.pack("<I", 36 + data_size) + b"WAVE"
    header += b"fmt " + struct.pack("<I", 16) + fmt
    header += b"data" + struct.pack("<I", data_size)
    return header + struct.pack(f"<{len(samples)}h", *samples)

out = wav_bytes(kept)
out_path = Path("/tmp/mobile_trimmed.wav")
out_path.write_bytes(out)
print(f"wrote {out_path} ({len(out)}B)")
print("first 8 kept:", kept[:8])
print("last 8 kept:", kept[-8:])
