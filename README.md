# Shazam Clone — Nhận diện nhạc bằng audio fingerprinting

Đồ án cuối kỳ DSP501. Nhận diện bài hát từ đoạn thu ngắn theo thuật toán
constellation hashing (Wang, 2003), với STFT tự cài bằng `numpy.fft`.

> Tài liệu đầy đủ — cài đặt, kiến trúc, giải thích thuật toán — được viết ở
> Phase 6. File này hiện chỉ đủ để chạy phần lõi DSP.

## Chạy thử phần lõi

```bash
uv sync
uv run pytest
uv run python scripts/visualize_pipeline.py <file.mp3|wav>
```

Kế hoạch triển khai: [`plans/260731-1847-shazam-clone-dsp/plan.md`](plans/260731-1847-shazam-clone-dsp/plan.md)
