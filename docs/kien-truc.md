# Kiến trúc hệ thống

Phần thuật toán nằm ở [`thuat-toan.md`](thuat-toan.md). File này nói về cách hệ
thống được lắp ráp và **vì sao** lại lắp như vậy.

## Luồng dữ liệu

```
nhạc (FMA / data/songs) ──build, 10 tiến trình──▶ PostgreSQL
                                                       ▲
                                                       │ tra cứu hash
  web/ React + Vite + TS ─┐                            │
   thu 8 s, tần số gốc    │                   src/shazam/  (lõi DSP)
                          │  POST /api/match  ├ audio.py    load → mono → 11025 Hz
                          ├──▶               ─┤ stft.py     STFT tự cài
                          │  POST /api/spec…  ├ peaks.py    constellation map
                          │      FastAPI ─────┤ hashing.py  cặp đỉnh → hash 32-bit
  desktop-app/ tkinter    │                   ├ database.py psycopg3, COPY, schema
   thu 5 s, 22 050 Hz ────┘                   └ matcher.py  histogram → điểm khớp

đóng gói: docker compose → db (postgres:18) + api (FastAPI phục vụ luôn web/dist)
```

Hai client, một backend. Cả hai chỉ gọi `POST /api/match` và `POST /api/spectrogram`;
server không biết và không cần biết bên kia là trình duyệt hay tkinter. Nhưng hai
client **thu âm khác nhau** — xem [quyết định #1](#1-toàn-bộ-resample-nằm-ở-server-qua-đúng-một-bộ-lọc).

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
| `desktop-app/` | Client tkinter một file, thu micro và hiển thị, không có DSP |

## Bề mặt HTTP

Bốn route, tất cả dưới `/api`. Đây là toàn bộ hợp đồng giữa backend và mọi client.

| Route | Vào | Ra |
|---|---|---|
| `POST /api/match` | multipart `file` | `{match, queryHashes, elapsedMs}`, `match` là `null` khi không tìm thấy |
| `POST /api/spectrogram` | multipart `file` | PNG spectrogram kèm constellation chồng lên |
| `GET /api/songs` | `limit` ≤ 200, `offset`, `q` | Danh sách bài trong kho, có tổng số |
| `GET /api/health` | — | Trạng thái, số bài, số vân tay (đếm lúc khởi động) |

**Không tìm thấy là `200`, không phải lỗi.** `match: null` là một câu trả lời
thật. Trả `404` sẽ khiến client phải phân biệt "bài không có trong kho" với
"gõ sai URL" — hai chuyện chẳng liên quan gì nhau.

Giới hạn tải lên là **10 MB**, chặn ở hai lớp: middleware từ chối theo
`content-length` *trước khi* đọc body, và `_read_upload` vẫn đọc dư một byte để
bắt trường hợp client khai man độ dài. Audio ngắn hơn **1 giây** bị từ chối với
`422`. Mọi thông điệp lỗi đều bằng tiếng Việt và không bao giờ lộ nguyên văn lỗi
của libsndfile ra ngoài.

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

**Desktop client hiện đi chệch quyết định này — và đây là chỗ cần biết trước khi
tin vào kết quả của nó.** `desktop-app/app.py` đặt `SAMPLE_RATE = 22_050` rồi
truyền thẳng vào `sd.InputStream`. Đó chính là "ép tần số" mà gạch đầu dòng trên
vừa cảnh báo: thiết bị hiếm khi chạy sẵn ở 22 050 Hz, nên CoreAudio (hoặc
PortAudio) tự hạ mẫu từ tần số gốc **trước khi ta thấy mẫu**, bằng một bộ lọc
không nằm trong repo và không đo được từ đây. Web client thì thu ở tần số gốc và
để `resample_poly` phía server làm nốt.

Chặng còn lại thì sạch: 22 050 → 11 025 là chia đôi đúng hệ số nguyên, vẫn qua
đúng bộ lọc chung ở `audio.py`. Vấn đề nằm ở chặng vô hình phía trước.

Cách sửa đã rõ: bỏ tham số `samplerate` để PortAudio trả về tần số gốc của thiết
bị, rồi ghi đúng tần số thật đó vào WAV header thay vì hằng số. **Cố ý hoãn lại**
— sửa một dòng trên đường thu âm mà không đo lại đúng là kiểu thay đổi "vẫn chạy"
nhưng âm thầm nhận diện kém đi, tức đúng cái bẫy mà cả quyết định này được viết
ra để tránh. Điều kiện để làm: đo tỷ lệ nhận diện trước và sau, trên micro thật
với kho đã dựng. Trước khi có số đo đó thì để nguyên.

**Chuẩn hoá đỉnh phía client thì không phải vấn đề tương tự.** `signal_to_wav_bytes`
chia tín hiệu cho biên độ đỉnh trước khi lượng tử hoá xuống int16. Khác với AGC ở
gạch đầu dòng trên, đây là **một hệ số tĩnh cho cả đoạn**: nó không bóp méo quan
hệ giữa các đỉnh phổ, và `audio.py` dù sao cũng chuẩn hoá lại phía server. Đổi lại,
đoạn thu nhỏ tiếng dùng hết được 16 bit thay vì nằm co cụm quanh 0.

**Độ dài đoạn thu cũng khác:** desktop 5 giây, web 8 giây, `shazam listen` mặc
định 8 giây. Đoạn ngắn hơn sinh ít hash hơn nên điểm thấp hơn, mà đoạn thu qua
micro vốn đã là loại cho điểm thấp nhất (xem quyết định #2). Ngưỡng `min_score`
20 được chọn từ số đo trên đoạn cắt từ file, **chưa đo lại cho đoạn micro 5 giây**.

### 2. Ngưỡng điểm tuyệt đối **và** kiểm tra tỷ lệ với á quân

Chỉ lấy đỉnh histogram cao nhất thì luôn trả về một bài nào đó, kể cả với tiếng
ồn. Phải thoả cả `score >= min_score` lẫn `score/á_quân >= 2.0`.

`min_score` là **20**, chọn từ số đo trên kho đủ 8 000 bài chứ không phải áng
chừng: bài đúng thấp nhất được 222 điểm, bài ngoài kho có trung vị 6 và p95 13.
Nâng từ 10 lên 20 bỏ được một nửa số ca nhận nhầm mà không mất bài đúng nào.
Không nên nâng cao hơn — đoạn thu qua micro cho điểm thấp hơn hẳn đoạn cắt từ
file, mà phần nhận nhầm còn lại là bản trùng nên ngưỡng không giải quyết được.

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

Kho đầy đủ: 7 993 bài FMA + 10 bài tổng hợp dùng để kiểm thử.

| Đại lượng | Ước lượng ban đầu | **Đo thật** |
|---|---|---|
| Số bài | 8 000 | **8 003** |
| Số vân tay | ~48 triệu | **78 485 849** |
| Hash khác nhau | — | 6 203 032 (trung bình **12,65** dòng/hash) |
| Bảng `fingerprints` | ~1,9 GB | **3,3 GB** |
| Index trên `hash` | ~1,4 GB | **2,4 GB** |
| Tổng database | ~3,5 GB | **6,2 GB** |
| Thời gian dựng kho | 8-12 phút | **3,8 phút** (6 993 bài, 10 tiến trình) |
| Thời gian dựng index | — | **49 s** |
| Mật độ đỉnh | 20-30/giây | **40,9/giây** |

Kho lớn gấp 1,63 lần ước lượng vì mật độ đỉnh thật là 40,9/giây chứ không phải
25 — đúng bằng tỷ lệ 40,9/25. Xem phần cuối mục này.

Thời gian từng bước cho một query 10 giây (tốt nhất trong 5 lần, kho đủ 8 003 bài):

| Bước | ms | % |
|---|---|---|
| Nạp + hạ mẫu | 2,8 | 7% |
| STFT | 1,0 | 3% |
| Lọc đỉnh | 3,6 | 10% |
| Băm | 0,6 | 2% |
| Tra cứu database | **29,7** | **79%** |
| **Tổng** | **37,8** | **264× thời gian thực** |

Đúng như dự đoán, tra cứu database là phần phình theo kích thước kho: từ 47% lên
**79%** tổng thời gian, còn bốn bước DSP thì gần như không đổi. Một query giờ
chạm 62 573 dòng thay vì 1 778.

Đo qua HTTP thật, kho đủ 8 003 bài:

| Loại truy vấn | Thời gian |
|---|---|
| **Đoạn 10 giây** (đúng thứ web app gửi) | **0,26-0,58 s** |
| File mp3 đầy đủ 30 giây | 0,82-2,52 s |

> Ngưỡng 2 giây **đạt với truy vấn thật** (web app thu 8 giây, `shazam listen`
> mặc định 8 giây, desktop app 5 giây). Nhưng gửi nguyên bài 30 giây thì đã chạm
> 2,52 s: truy vấn dài gấp ba sinh hash gấp ba và tra cứu nặng gấp ba. Đây là
> ràng buộc cần biết trước khi tăng kích thước kho thêm nữa.
>
> Desktop app gọi **hai** endpoint cho mỗi lần nhận diện — `/api/match` rồi
> `/api/spectrogram`, cùng một tệp WAV tải lên hai lần — nên tổng thời gian người
> dùng chờ là tổng của hai lượt. Web app chỉ xin spectrogram khi người dùng tự
> tích chọn.

### Chất lượng nhận diện ở quy mô thật

Phép thử dùng chính nhạc trong bộ dữ liệu làm bài "ngoài kho": giữ lại một nhóm
bài (xoá khỏi database) rồi truy vấn bằng chính chúng, sau đó nạp lại. Khó hơn
nhiều so với thử bằng tiếng ồn trắng.

| Mẫu | Nhận đúng | Từ chối đúng bài ngoài kho |
|---|---|---|
| n=120, `min_score` 10 | 118/120 | 114/120 |
| n=60, `min_score` 20 | **60/60** | **57/60** |

Sai số còn lại gần như đều là **bản ghi trùng có thật trong `fma_small`** — 145
bài nằm trong 66 nhóm trùng tên+nghệ sĩ. Quan sát trực tiếp:
`DREEMER` → `Dreemer` (điểm 1 938), `Back To It (w/ Daddy-O of Stetsasonic)` →
`Back To It (12inch Mixx)` (660). Đây là cùng bản ghi nằm trong kho dưới hai mã
khác nhau, không ngưỡng nào tách được.

Bản trùng cũng gây ra chiều ngược lại, đúng như quyết định #2 đã lường: khi bài
bị giữ lại có bản sao còn trong kho thì á quân ngang quán quân, tỷ lệ ≈ 1,00 và
hệ thống **từ chối đoán**. Đó là hành vi đúng, không phải lỗi.

Môi trường: Python 3.14.6, numpy 2.5.1, scipy 1.18.0, soundfile 0.14.0 (libsndfile
1.2.2, đọc MP3 nên **không cần ffmpeg**), FastAPI 0.141.1, PostgreSQL 18.4,
Node 24.15.0. Máy 10 nhân.

## Những thứ cố ý không làm

- **Client mobile.** Backend đã đủ để dùng lại mà không sửa gì — desktop client
  đã chứng minh điều đó: thêm nó vào không phải sửa một dòng nào ở `src/`.
- **Đóng gói desktop client thành file chạy được.** Không có PyInstaller,
  py2app hay spec nào trong repo; chạy bằng `python desktop-app/app.py`.
- **Cắt bỏ hash quá phổ biến.** Kho đủ 8 003 bài trung bình **12,65 dòng/hash**,
  hash phổ biến nhất xuất hiện 616 lần. Tra cứu đã chiếm 79% thời gian query
  nhưng tổng vẫn chỉ 0,26–0,58 s, còn xa ngưỡng 2 giây — nên chưa cắt. Đây là
  chỗ cần xử lý đầu tiên nếu kho lớn hơn nữa.
- **Chỉnh mật độ đỉnh.** Đo trên nhạc thật (12 bài FMA, 360 giây): **40,9
  đỉnh/giây**, cao hơn khoảng ước lượng 20–30. Vẫn không chỉnh, nhưng nay là
  quyết định có số liệu chứ không phải hoãn: mật độ cao làm kho phình 1,63 lần
  (78,5 triệu vân tay thay vì 48 triệu) mà đổi lại khoảng cách giữa bài đúng và
  bài sai rộng ra — bài đúng thấp nhất 222 điểm, bài lạ p95 chỉ 13. Đĩa thì
  thừa, còn biên an toàn ấy thì đáng giá, nhất là với đoạn thu qua micro.
- **Xác thực người dùng, triển khai công khai, CI/CD.**
- **Làm mới số đếm ở `/api/health`.** Đếm một lần lúc khởi động rồi lưu đệm; sau
  khi dựng kho phải khởi động lại API mới thấy số mới. Đổi lại là `/health` không
  bao giờ quét bảng 78,5 triệu dòng.
