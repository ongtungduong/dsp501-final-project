# Nhận diện nhạc bằng audio fingerprinting — DSP501

Nghe một đoạn ngắn 5–10 giây rồi tìm ra đó là bài nào, theo thuật toán
constellation hashing của Shazam (Wang, 2003).

Trọng tâm học thuật nằm ở phần xử lý tín hiệu: **STFT tự cài bằng `numpy.fft`**,
lọc đỉnh phổ, băm cặp đỉnh thành mã bất biến với dịch thời gian, và khớp bằng
histogram độ lệch thời gian. Không gọi `scipy.signal.stft` — có test chặn việc đó
quay lại.

![Constellation map](docs/images/000010-constellation.png)

- **Thuật toán, giải thích từng bước kèm hình:** [`docs/thuat-toan.md`](docs/thuat-toan.md)
- **Kiến trúc và các quyết định thiết kế:** [`docs/kien-truc.md`](docs/kien-truc.md)

## Chạy bằng Docker

Cần Docker. Không cần cài Python hay Node.

```bash
cp .env.example .env          # rồi đặt POSTGRES_PASSWORD
docker compose up -d          # dựng cả PostgreSQL lẫn backend
```

Mở <http://localhost:8000>. Web app do chính backend phục vụ, không cần chạy
thêm gì.

Kho nhạc lúc này còn rỗng. Nạp nhạc theo một trong hai cách:

```bash
# Cách A — nhạc của bạn: chép file vào data/songs/ rồi
docker compose run --rm api shazam build --source local
docker compose run --rm api shazam create-index

# Cách B — bộ Free Music Archive, 8.000 bài (tải 7,2 GB, chậm)
docker compose run --rm api shazam fetch
docker compose run --rm api shazam build --source fma
docker compose run --rm api shazam create-index
```

`shazam fetch` tải tiếp được sau khi ngắt: chạy lại là nó đi tiếp từ chỗ dừng,
và kiểm SHA1 trước khi dùng.

## Chạy trực tiếp để phát triển

Cần Python 3.14 + [uv](https://docs.astral.sh/uv/), Node 24 + pnpm, và Docker cho
PostgreSQL.

```bash
uv sync
cd web && pnpm install && cd ..

docker compose up -d db                       # chỉ PostgreSQL
uv run shazam init-db
uv run shazam build --source local            # nhạc trong data/songs/
uv run shazam create-index
```

Rồi mở hai terminal:

```bash
DATABASE_URL=postgresql://shazam:shazam@localhost:5432/shazam \
  uv run uvicorn server.app:app --reload --port 8000
cd web && pnpm dev                            # http://localhost:5173
```

Vite proxy sẵn `/api` về cổng 8000 nên không vướng CORS lúc phát triển.

## Lệnh CLI

| Lệnh | Việc |
|---|---|
| `shazam init-db` | Tạo schema |
| `shazam fetch [--source fma]` | Tải bộ FMA, tải tiếp được, kiểm SHA1 |
| `shazam build [--source local\|fma\|all] [--limit N] [--workers N]` | Dựng kho, chạy song song nhiều tiến trình |
| `shazam create-index` | Dựng index — **chạy sau khi build xong** |
| `shazam match <file>` | Nhận diện một file audio |
| `shazam listen [--seconds 8]` | Nhận diện từ micro |
| `shazam stats` | Số bài, số vân tay, phân bố tần suất hash |

`create-index` tách riêng khỏi `build` là có chủ đích: duy trì index trong lúc
nạp hàng chục triệu dòng chậm hơn hẳn so với dựng một lần sau cùng.

## Kiểm thử

```bash
uv run pytest                 # 108 test
uv run ruff check .
uv run mypy                   # --strict
cd web && pnpm build && pnpm test
```

Test database tự bỏ qua khi không có PostgreSQL, nên bộ test đơn vị chạy được ở
mọi nơi.

## Sinh lại hình minh hoạ

```bash
# Bốn hình cho bốn bước đầu: hạ mẫu, spectrogram, constellation, ghép cặp đỉnh
uv run python scripts/visualize_pipeline.py data/fma/fma_small/000/000010.mp3 --out docs/images

# Hình histogram khớp đúng / khớp sai — cần kho đã dựng
uv run python scripts/visualize_matching.py data/queries/q-10s.wav --out docs/images

uv run python scripts/benchmark_pipeline.py data/queries/q-10s.wav
```

## Xử lý sự cố

**Không xin được quyền micro trên macOS.** System Settings › Privacy & Security ›
Microphone, bật cho trình duyệt. Trình duyệt chỉ cho dùng micro ở *secure
context*; `localhost` được miễn nên demo trên máy tính không vướng. Desktop
app dùng tkinter nên thuộc quyền của Terminal / `python3` — xem
[`desktop-app/README.md`](desktop-app/README.md) để biết cách cấp trên từng
hệ điều hành.

**Muốn demo từ điện thoại.** Cần HTTPS. Dựng đường hầm tạm:

```bash
cloudflared tunnel --url http://localhost:8000
```

**Thu xong không ra bài nào.** Để máy gần loa hơn, tăng âm lượng, thu đủ 8–10
giây. Nếu bài đó không có trong kho thì hệ thống trả "không tìm thấy" — đó là
hành vi đúng, không phải lỗi: xem [quyết định #2](docs/kien-truc.md).

**Dựng kho lâu.** Dùng `--limit 100` để thử trước với vài trăm bài rồi mới chạy
đủ kho.

**Tải FMA đứt giữa chừng.** Chạy lại `shazam fetch`. Nó đọc kích thước file cục
bộ, xin phần còn thiếu bằng header `Range`, và kiểm SHA1 trước khi giải nén.

**`docker compose up` báo thiếu `POSTGRES_PASSWORD`.** Chưa tạo `.env`. Đây là cố
ý — compose từ chối khởi động còn hơn lặng lẽ dùng mật khẩu mặc định yếu.

**Dựng kho xong mà `/api/health` vẫn báo 0 bài.** Đúng như thiết kế: số đếm được
lấy một lần lúc API khởi động rồi lưu đệm, vì `COUNT(*)` trên bảng vân tay là
quét toàn bảng và không được phép nằm trên đường đi của mỗi request. Nhận diện
vẫn chạy đúng ngay lập tức; chỉ con số hiển thị là cũ. Khởi động lại để cập nhật:

```bash
docker compose restart api
```

**Đổi tham số DSP rồi thì phải dựng lại kho.** Vân tay cũ tính theo tham số cũ,
trộn hai loại vào một kho thì không khớp được.

## Desktop app

Python tkinter, ghi âm rồi gửi clip lên API. Cùng hai endpoint mà web
client dùng — `POST /api/match` và `POST /api/spectrogram` — nên backend
không cần biết client nào đang gọi.

```bash
uv pip install -r desktop-app/requirements.txt
python desktop-app/app.py
```

Ô **API URL** ở thanh trên cùng cho phép trỏ sang server khác
(cổng khác, máy khác). Cũng đặt được bằng `API_BASE_URL` trong `.env`
hoặc biến môi trường. Chi tiết quyền micro theo hệ điều hành và xử lý
lỗi trong [`desktop-app/README.md`](desktop-app/README.md).

## Phạm vi

Bản này gồm lõi DSP, backend, web app và desktop client. Tất cả client
đều gọi chung hai endpoint HTTP ở `src/server/routes.py` — backend
không phân biệt client nào đang nói chuyện với nó.

Hạn chế cố hữu của thuật toán: không nhận được bản cover, hát lại hay remix
(vân tay bám vào đúng bản ghi cụ thể), và nhạy với thay đổi tốc độ phát. Chi tiết
trong [`docs/thuat-toan.md`](docs/thuat-toan.md).
