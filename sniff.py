import struct
from pathlib import Path

def analyze(path):
    data = path.read_bytes()
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
    n = data_size // 2
    samples = struct.unpack(f"<{n}h", data[data_off:data_off+data_size])
    # Drop 1 sample if signed max comes from clipping (32768)
    peak = max(samples); minv = min(samples)
    abs_samples = [abs(x) for x in samples]
    dc = sum(samples) / n
    rms = (sum(x*x for x in samples) / n) ** 0.5
    nonzero = sum(1 for x in abs_samples if x > 100)
    unique = len(set(samples))
    print(f"{path.name}  n={n}  dur={n/22050:.2f}s")
    print(f"  min={minv}  max={peak}  dc_offset={dc:.2f}")
    print(f"  rms={rms:.2f}  peak={max(abs_samples)}  ratio(rms/peak)={rms/max(abs_samples):.3f}")
    print(f"  unique_samples={unique}  nonzero(|x|>100)={nonzero}/{n}  ({100*nonzero/n:.1f}%)")
    print(f"  first 8: {samples[:8]}")
    print(f"  last 8:  {samples[-8:]}")

analyze(Path("data/uploads/20260815T135853_d207e2f63efa45c3a9f725fbf146928e.wav"))
analyze(Path("data/uploads/20260815T135830_e9e41a796b0a4e58aa1d3cf59c3a2ac9.wav"))
