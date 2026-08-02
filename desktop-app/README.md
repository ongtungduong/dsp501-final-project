# Desktop Client — Nhận diện âm thanh

App tkinter nhỏ ghi âm từ micro mặc định, gửi đoạn thu được lên HTTP API
của dự án để nhận diện, rồi hiển thị kết quả kèm ảnh phổ trả về từ server.

## Cách hoạt động

1. Bấm **Bắt đầu nhận diện** để bắt đầu ghi âm.
2. App thu **5 giây** âm thanh mono, tần số lấy mẫu 22 050 Hz, từ thiết bị
   được chọn trong dropdown **Mic** (mặc định: thiết bị vào mặc định của
   hệ thống).
3. Đoạn thu được mã hoá thành WAV PCM 16-bit rồi gửi **hai lần** lên server:
   - `POST /api/match` — trả về JSON kết quả khớp
   - `POST /api/spectrogram` — trả về ảnh PNG spectrogram
4. Panel kết quả hiển thị tên bài / nghệ sĩ / điểm khớp; panel spectrogram
   vẽ ảnh PNG ngay trong cửa sổ. Một ô **Response JSON** ở dưới cùng in
   raw response của cả hai endpoint — header (status, content-type,
   content-length) cho cả `/api/match` và `/api/spectrogram`, body JSON
   đẹp cho `/api/match`, và ghi chú `<... bytes of PNG>` cho
   `/api/spectrogram` (vì body là ảnh, không in được).

Cả hai endpoint định nghĩa ở `src/server/routes.py` và giải mã WAV theo
đúng đường ống của bộ khớp vân tay.

## Cài đặt

App dùng `tkinter` (có sẵn trong CPython) và lấy thêm 4 gói nhẹ. Có hai
cách cài:

**Cách A — dùng venv của dự án (khuyến nghị).** Cùng môi trường với server,
không lệch phiên bản `numpy` / `sounddevice`:

```bash
uv pip install -r desktop-app/requirements.txt
```

**Cách B — venv riêng.** Phù hợp khi chạy desktop app trên máy không có
sẵn mã nguồn phần server:

```bash
python -m venv .desktop-venv
# Windows
.\.desktop-venv\Scripts\activate
# macOS / Linux
source .desktop-venv/bin/activate

pip install -r desktop-app/requirements.txt
```

## Chọn thiết bị mic

Thanh trên cùng có dropdown **Mic** liệt kê mọi thiết bị vào (input) mà
PortAudio nhìn thấy. Mỗi mục hiển thị `<index>: <tên thiết bị>` — index
là chỉ số nội bộ của PortAudio, dùng để phân biệt khi hai thiết bị trùng
tên. Lựa chọn mặc định, **Mặc định của hệ thống**, nghĩa là PortAudio giải
thiết bị mặc định của OS tại thời điểm bấm ghi — khi bạn đổi thiết bị
mặc định trong cài đặt âm thanh của hệ thống trong khi app đang mở thì
lần ghi tiếp theo vẫn theo dõi đúng.

Chỉ những thiết bị có `max_input_channels > 0` mới được liệt kê — một
số máy ảo hoặc USB DAC tạo ra "thiết bị" chỉ có chiều ra. Trên máy
chưa cắm mic thì chỉ có mục mặc định.

Nút **↻** ngay cạnh dropdown re-query thiết bị từ `sounddevice`. Cắm
USB mic vào sau khi app đã mở xong, bấm **↻** rồi chọn thiết bị mới.
Lựa chọn thiết bị được giữ nguyên khi refresh; nếu tên thiết bị cũ
không còn thì app trả về mặc định của hệ thống để lần ghi tiếp theo
vẫn chạy được.

Dropdown **bị khóa** trong khi đang ghi hoặc đang xử lý. Đổi thiết bị
giữa chừng sẽ ghi nhận nhưng không ảnh hưởng đến lần ghi đang chạy — chỉ
áp dụng cho lần ghi tiếp theo.

## Chạy

App **chỉ gọi HTTP**, không tự dựng server. Khởi động server trước bằng
một trong hai cách:

```bash
# Docker (cả PostgreSQL lẫn backend)
docker compose up -d

# Hoặc dev (cần Docker cho PostgreSQL, còn lại chạy trên host)
docker compose up -d db
uv run uvicorn server.app:app --port 8000
```

Rồi chạy app:

```bash
python desktop-app/app.py
```

Cửa sổ mở ra với ô **API URL** ở trên cùng (mặc định `http://127.0.0.1:8000`).
Đổi giá trị này nếu server chạy ở máy khác / cổng khác. Cũng có thể đặt
trước qua biến môi trường:

```bash
# Windows (PowerShell)
$env:API_BASE_URL = "http://192.168.1.20:8000"
python desktop-app/app.py

# macOS / Linux
API_BASE_URL=http://192.168.1.20:8000 python desktop-app/app.py
```

Biến môi trường cũng đọc được từ `.env` ở thư mục gốc dự án (chỉ khoá
`API_BASE_URL`).

## Lưu ý theo nền tảng

**Windows.** Chạy trực tiếp `python desktop-app/app.py`. Hệ thống xin
quyền micro ở lần đầu tiên — chọn Allow cho Terminal / Python.

**macOS.** System Settings › Privacy & Security › Microphone, bật quyền
cho Terminal (hoặc cho `python3` tuỳ cách gọi). Cấp xong phải chạy lại
app để nhận quyền mới.

**Linux.** Cần `libportaudio2` (Debian/Ubuntu) hoặc tương đương. PulseAudio
/ PipeWire phải đang chạy và thiết bị vào mặc định phải tồn tại
(`arecord -l` để liệt kê).

## Cấu trúc file

- `app.py` — Toàn bộ app. Gồm:
  - `AudioRecorder` — mở mic, ghi vào `np.ndarray` qua callback
  - `signal_to_wav_bytes` — mã hoá float32 sang PCM 16-bit, peak-normalise
  - `MatchResult.from_json` — parse JSON trả về từ `/api/match` theo
    schema trong `src/server/schemas.py` (camelCase)
  - `ShazamDesktopApp` — UI tkinter. Mọi cập nhật widget được marshal
    về main thread qua `root.after()` để không đụng tk từ worker thread
- `requirements.txt` — `sounddevice`, `numpy`, `requests`, `pillow`.
  Server đã yêu cầu `sounddevice` và `numpy` nên phần mới thực sự là
  `requests` và `pillow`.

## Xử lý sự cố

**App báo "Lỗi micro".** Hệ điều hành không cho app truy cập mic. Xem
mục "Lưu ý theo nền tảng" ở trên.

**Server trả 422 — "Đoạn audio quá ngắn".** Micro mở nhưng không nhận
được tín hiệu (nhầm thiết bị, mic bị mute cứng). Chọn đúng thiết bị vào
trong cài đặt âm thanh của hệ điều hành rồi thử lại.

**Server trả 5xx / connection error.** Server chưa chạy, hoặc `API URL`
trong ô nhập sai. Đặt ô về `http://127.0.0.1:8000` rồi bấm lại.

**Thu xong mà báo "Không nhận ra".** Đúng như thiết kế — bài đó không
nằm trong kho. Đưa máy lại gần loa, tăng âm lượng, hoặc dựng kho thêm
nhạc bằng `shazam build --source local` sau khi chép file vào `data/songs/`.
