# Kiến trúc hệ thống

Phần thuật toán nằm ở [`thuat-toan.md`](thuat-toan.md). File này nói về cách hệ
thống được lắp ráp và **vì sao** lại lắp như vậy.

## Luồng dữ liệu

```
nhạc (FMA / data/songs)  ──build, 10 tiến trình──▶  PostgreSQL
                                                         ▲
                                                         │ tra cứu hash
  React + Vite + TS                                      │
  ├ thu mic (AudioWorklet)          src/shazam/  (lõi DSP)
  ├ đóng gói WAV (không xử lý)      ├ audio.py      load → mono → 11025 Hz
  ├ POST /api/match ──▶ FastAPI ────┤ stft.py       STFT tự cài
  └ hiện tên bài + độ mạnh          ├ peaks.py      constellation map
                                    ├ hashing.py    cặp đỉnh → hash 32-bit
                                    ├ database.py   psycopg3, COPY, schema
                                    └ matcher.py    histogram → điểm khớp

đóng gói: docker compose → db (postgres:18) + api (FastAPI phục vụ luôn web/dist)
```

## Trách nhiệm từng module

| Module | Việc |
|---|---|
| `config.py` | Toàn bộ tham số DSP và ngưỡng khớp, ở một chỗ |
| `audio.py` | Giải mã, mono hoá, hạ mẫu, chuẩn hoá — **lối vào duy nhất** |
| `stft.py` | STFT trên `numpy.fft`, không dùng scipy |
| `peaks.py` | Spectrogram → constellation map |
| `hashing.py` | Ghép cặp đỉnh → hash bất biến dịch thời gian |
| `fingerprint.py` | Nối ba bước trên, để không ai lắp sai thứ tự |
| `database.py` | Schema, `COPY` nhị phân, tra cứu, histogram phía SQL |
| `matcher.py` | Chấm điểm, ngưỡng kép, quyết định khớp hay không |
| `builder.py` | Dựng kho song song nhiều tiến trình |
| `sources/` | Nguồn nhạc: `LocalSource`, `FmaSource` |
| `server/` | Vỏ HTTP mỏng, không có logic DSP |
| `web/` | Thu micro và hiển thị, không có DSP |

## Lược đồ PostgreSQL

```sql
CREATE TABLE songs (
    id       SERIAL PRIMARY KEY,
    title    TEXT NOT NULL,
    artist   TEXT,
    path     TEXT NOT NULL UNIQUE,   -- khoá danh mục, không phải đường dẫn
    duration REAL,
    source   TEXT NOT NULL DEFAULT 'local'
);

CREATE TABLE fingerprints (
    hash     BIGINT  NOT NULL,
    song_id  INTEGER NOT NULL REFERENCES songs(id) ON DELETE CASCADE,
    "offset" INTEGER NOT NULL
);

-- dựng SAU khi nạp xong toàn bộ
CREATE INDEX idx_fingerprints_hash
    ON fingerprints USING btree (hash) INCLUDE (song_id, "offset");
CREATE INDEX idx_fingerprints_song_id ON fingerprints (song_id);
```

**Vì sao `COPY` nhị phân chứ không `executemany`.** Mỗi dòng qua `executemany`
phải chịu một vòng phân tích cú pháp và một lượt qua mạng. `COPY` đẩy thẳng
luồng byte vào server. Ở quy mô hàng chục triệu dòng đây là khác biệt giữa "vài
phút" và "vài giờ".

**Vì sao dựng index sau.** Duy trì b-tree qua từng lần chèn tốn hơn hẳn so với
sắp xếp một lần ở cuối. `create_index` nâng `maintenance_work_mem` lên 1 GB trước
khi dựng, rồi chạy `ANALYZE` — sau một mẻ `COPY` lớn, planner chưa có thống kê gì.

**Vì sao index có `INCLUDE`.** Không có nó, mỗi query phải nhảy vào heap để lấy
`song_id` và `offset` cho từng dòng trùng. Kèm hai cột đó vào index thì truy vấn
đọc xong ngay trong index, không chạm heap.

**Vì sao có index trên `song_id`.** Khoá ngoại có `ON DELETE CASCADE`; cột con
không index nghĩa là xoá một bài phải quét toàn bảng. Không có nó thì việc
fingerprint lại một bài — chuyện hiển nhiên phải làm sau khi chỉnh tham số DSP —
gần như bất khả thi.

**Vì sao gộp histogram trong SQL.** Kéo hết dòng trùng về Python rồi mới đếm
nghĩa là chuyển hàng trăm nghìn dòng qua dây, dựng lại thành tuple Python, đếm,
rồi vứt gần hết. `lookup_histogram` gộp ngay trong database và trả về một dòng
cho mỗi bài ứng viên.

## Dựng kho song song

Trích vân tay là việc thuần CPU và độc lập giữa các bài, nên chia cho nhiều tiến
trình thắng gần tuyến tính. Ghi database thì không: chỉ **tiến trình chính** ghi.

```
Pool(n_workers) ──▶ worker: giải mã → STFT → đỉnh → hash
                        │ trả về (metadata, hashes)
                        ▼
              tiến trình chính: INSERT song → COPY fingerprints
```

Worker chỉ tính, cha ghi. Tránh tranh chấp kết nối, giao dịch đơn giản, mà vẫn
dùng hết số nhân cho phần nặng.

**Worker không bao giờ ném ngoại lệ** — lỗi được trả về dưới dạng dữ liệu, vì FMA
có sẵn vài file cụt và một file hỏng không được phép giết cả mẻ 8.000 bài.

**Kết quả lấy về kèm timeout.** Worker *ném* lỗi thì đã xử lý ở trên, nhưng
worker *chết hẳn* — segfault trong bộ giải mã, hoặc bị OOM killer — thì không ném
gì cả, và pool sẽ chờ mãi một kết quả không bao giờ tới. Đã đo: mẻ dựng trả về
mọi bài khác rồi treo vĩnh viễn, không lỗi, không tổng kết.

## Bốn quyết định thiết kế then chốt

### 1. Toàn bộ resample nằm ở server, qua đúng một bộ lọc

Trình duyệt gửi WAV ở tần số gốc của `AudioContext` (macOS thường 48 kHz) và
**không xử lý gì**. Nếu JavaScript tự hạ mẫu bằng thuật toán khác thì tín hiệu
query đi con đường khác lúc dựng kho, vân tay lệch, tỷ lệ nhận diện tụt — mà hệ
thống vẫn "chạy được" nên rất khó truy.

Cùng một *hàm* là chưa đủ, phải cùng một *bộ lọc*: xem bảng đo trong
[`thuat-toan.md`](thuat-toan.md#bước-1--chuẩn-hoá-và-hạ-tần-số-lấy-mẫu).

Quyết định này còn kéo theo hai chỗ dễ bỏ sót:

- **Micro thu ở tần số gốc của thiết bị**, không ép 44 100. Xin sai tần số thì
  CoreAudio tự resample trước khi ta thấy mẫu — thành đường xử lý thứ ba, vô
  hình từ trong mã.
- **Tắt echo cancellation, noise suppression và AGC** khi xin micro. Trình duyệt
  bật cả ba theo mặc định, và cả ba đều là xử lý tín hiệu áp lên trước khi ta
  nhận được mẫu: AGC đổi độ lợi theo thời gian (chuẩn hoá tĩnh phía server không
  gỡ lại được), noise suppression làm yếu đúng các thành phần tông ổn định mà
  peak picking chọn, còn echo cancellation thì chủ động triệt phần âm thanh
  tương quan với thứ máy đang phát — tức đúng kịch bản demo.

### 2. Ngưỡng điểm tuyệt đối **và** kiểm tra tỷ lệ với á quân

Chỉ lấy đỉnh histogram cao nhất thì luôn trả về một bài nào đó, kể cả với tiếng
ồn. Phải thoả cả `score >= min_score` lẫn `score/á_quân >= 2.0`.

Hệ quả không hiển nhiên: **bản trùng trong kho phá vỡ quyết định này**. Hai bản
của cùng một bài chia đôi histogram, á quân ngang bằng quán quân, và bài đang có
trong kho bị trả về "không tìm thấy". Vì thế khoá chống trùng phải chuẩn — xem
quyết định #5.

### 3. Cộng epsilon trước khi lấy log biên độ

Đo được: đoạn im lặng 50×50 không epsilon cho 2500/2500 đỉnh giả; có epsilon cho
0. Dùng `20*np.log10(mag + 1e-10)`.

### 4. Cột `hash` phải là `BIGINT`

Bin neo tối đa 512, `512 << 22 = 2 147 483 648`, vượt trần `INTEGER` có dấu đúng
1 đơn vị. Dùng `INTEGER` chỉ hỏng vân tay neo ở bin trên cùng — hỏng một phần,
phụ thuộc phổ, cực khó truy.

### 5. Khoá danh mục, không phải đường dẫn *(phát sinh khi chạy Docker)*

Ràng buộc `UNIQUE` trên `path` là thứ cho phép dựng kho tiếp sau khi ngắt. Nhưng
cùng một file có đường dẫn khác nhau ở host (`/Users/.../data/songs/x.wav`) và
trong container (`/app/data/songs/x.wav`). Dựng ở cả hai nơi — mà README **bảo**
người dùng dựng trong container — cho ra **20 bài từ 10 file**, và theo quyết
định #2 thì nhận diện hỏng hẳn.

Nay mỗi nguồn sinh một khoá ổn định: `local:<đường dẫn tương đối>`,
`fma:<track_id>`. Giống nhau ở mọi máy, mọi điểm mount.

## Kiến trúc Docker

Hai service: `db` (postgres:18) và `api`. Web app **không** cần service riêng —
nó là file tĩnh, để FastAPI phục vụ luôn thì demo chỉ còn một lệnh và không phát
sinh vấn đề CORS ở môi trường chạy thật.

`StaticFiles` phải gắn **sau** các route `/api/*`: mount ở `/` khớp mọi đường
dẫn, gắn trước thì nó nuốt luôn API và trả `index.html`.

**Vì sao mount `./data` thay vì COPY vào image.** Nhạc là dữ liệu, không phải mã
nguồn. Nhồi 15 GB vào image cho ra image khổng lồ và mỗi lần đổi một dòng code
lại phải dựng lại toàn bộ.

`.dockerignore` phải viết **trước** `Dockerfile`: thiếu nó thì Docker nuốt cả
`data/` vào build context và lệnh build đầu tiên treo trước khi chạy chỉ thị nào.

## Số đo thật

Kho hiện tại (10 bài tổng hợp, dùng để phát triển và kiểm thử):

| Đại lượng | Giá trị |
|---|---|
| Số bài | 10 |
| Số vân tay | 75 270 |
| Hash khác nhau | 69 067 (trung bình 1,09 dòng/hash) |
| Bảng `fingerprints` | 5,9 MB |
| Index trên `hash` | 2,1 MB |
| Thời gian dựng kho | 1,1 s với 10 tiến trình |
| Mật độ đỉnh | 34 đỉnh/giây |

Thời gian từng bước cho một query 10 giây (tốt nhất trong 5 lần):

| Bước | ms | % |
|---|---|---|
| Nạp + hạ mẫu | 2,6 | 19% |
| STFT | 1,0 | 7% |
| Lọc đỉnh | 3,0 | 22% |
| Băm | 0,6 | 5% |
| Tra cứu database | 6,5 | 47% |
| **Tổng** | **13,7** | **732× thời gian thực** |

Qua HTTP trong container: 16–19 ms cho toàn bộ vòng đời request.

> **Chưa đo ở quy mô 8.000 bài.** Bộ FMA đang tải dở. Ước lượng của kế hoạch là
> ~48 triệu dòng, ~3,5 GB; các số trên là kho nhỏ và **không** nên suy rộng ra —
> tra cứu database đã chiếm 47% và nó là phần tăng theo kích thước kho.

Môi trường: Python 3.14.6, numpy 2.5.1, scipy 1.18.0, soundfile 0.14.0 (libsndfile
1.2.2, đọc MP3 nên **không cần ffmpeg**), FastAPI 0.141.1, PostgreSQL 18.4,
Node 24.15.0. Máy 10 nhân.

## Những thứ cố ý không làm

- **Client mobile và desktop.** Backend đã đủ để dùng lại mà không sửa gì.
- **Cắt bỏ hash quá phổ biến.** `shazam stats` đo được phân bố tần suất, nhưng
  kho hiện tại trung bình 1,09 dòng/hash nên chưa có đuôi dài để cắt. Quyết định
  khi có kho thật.
- **Chỉnh mật độ đỉnh.** 34 đỉnh/giây cao hơn khoảng mục tiêu 20–30, nhưng số này
  đo trên nhạc tổng hợp tự sinh. Chỉnh theo nó là chỉnh theo artefact của chính
  mình; để lại cho tới khi có nhạc thật.
- **Xác thực người dùng, triển khai công khai, CI/CD.**
- **Làm mới số đếm ở `/api/health`.** Đếm một lần lúc khởi động rồi lưu đệm; sau
  khi dựng kho phải khởi động lại API mới thấy số mới. Đổi lại là `/health` không
  bao giờ quét bảng 48 triệu dòng.
