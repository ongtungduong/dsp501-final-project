# Mobile App — Nhận diện âm thanh (Flutter)

Flutter client cho backend nhận diện nhạc bằng audio fingerprinting
(DSP501). Bấm một nút, thu 8 giây từ micro, chuẩn hoá đỉnh trước khi gửi,
rồi gửi lên hai endpoint của FastAPI hiện có (`/api/match` và
`/api/spectrogram`), hiển thị tên bài / nghệ sĩ / điểm khớp cùng ảnh phổ
trả về từ server.

Cùng hai endpoint mà web client và desktop app dùng — backend không cần
biết client nào đang gọi.

## Cài đặt

Yêu cầu Flutter SDK 3.22+ (Dart 3.4+).

```bash
cd mobile-app
flutter pub get
```

Backend phải chạy trước khi mở app:

```bash
# từ thư mục gốc dự án
docker compose up -d
# hoặc
uv run uvicorn server.app:app --port 8000
```

## Chạy

Trỏ app về backend qua `--dart-define`. Địa chỉ mặc định trong
`lib/config.dart` là `http://10.0.2.2:8000` — đúng cho Android
emulator. Đối với thiết bị thật, dùng IP LAN của máy chạy backend.

```bash
# Android emulator
flutter run

# Android thiết bị thật (qua IP LAN)
flutter run --dart-define=API_BASE_URL=http://192.168.1.20:8000

# iOS simulator
flutter run -d ios

# Tất cả device đang kết nối
flutter devices
flutter run -d <device-id>
```

URL cũng có thể đổi ngay trong app (ô **API URL** ở đầu màn hình).

## Build APK / IPA

```bash
# Android APK debug
flutter build apk --debug --dart-define=API_BASE_URL=http://192.168.1.20:8000

# Android APK release (cần keystore)
flutter build apk --release --dart-define=API_BASE_URL=https://api.example.com

# iOS (cần macOS + Xcode)
flutter build ios --dart-define=API_BASE_URL=http://192.168.1.20:8000
```

File APK nằm ở `build/app/outputs/flutter-apk/app-release.apk`.

## Quyền micro

### Android

Lần đầu bấm nút, hệ thống sẽ hiện prompt xin quyền `RECORD_AUDIO`.
Chọn **Allow**.

Nếu trước đó đã từ chối, vào **Settings › Apps › Shazam › Permissions
› Microphone** rồi bật lại.

### iOS

`Info.plist` đã khai báo `NSMicrophoneUsageDescription` — lần đầu
bấm nút, iOS sẽ hiện prompt tiếng Việt "Cần micro để ghi âm đoạn nhận
diện."

Từ chối vĩnh viễn: **Settings › Shazam › Microphone** rồi bật lại.

## Cấu trúc

```
lib/
  main.dart                    MaterialApp + HomeScreen
  config.dart                  Sample rate, duration, API base, paths
  models/
    match_result.dart          MatchInfo + MatchResponse, camelCase
  services/
    api_client.dart            matchAudio() + getSpectrogram() + ApiError
    recorder_service.dart      bọc plugin `record`, xin quyền mic
    wav_normalizer.dart        chuẩn hoá đỉnh WAV trước khi upload
  screens/
    home_screen.dart           toàn bộ flow: thu → chuẩn hoá → upload → hiển thị
  widgets/
    record_button.dart         nút 3 trạng thái
    match_card.dart            bài hát / nghệ sĩ / score / strength
    spectrogram_view.dart      PNG bytes -> Image.memory
    api_log_panel.dart         nhật ký request/response gần nhất, phục vụ debug
test/
  match_result_test.dart       parse JSON, strength enum, edge cases
  widget_test.dart             RecordButton: nhãn + khoá nút theo trạng thái
android/
  app/src/main/AndroidManifest.xml    RECORD_AUDIO + INTERNET
ios/
  Runner/Info.plist                    NSMicrophoneUsageDescription
```

## Tham số ghi âm (bám sát desktop/web)

| Tham số | Giá trị | Lý do |
|---|---|---|
| Sample rate | 22050 Hz | Khớp rate corpus đã build, không resample |
| Channels | 1 (mono) | Stereo không tăng độ chính xác khớp |
| Duration | 8 giây | Khớp web + `shazam listen`, đủ hash cho điểm khớp cao hơn 6 s cũ |
| Encoder | WAV PCM 16-bit | Server decode qua `soundfile`, opus bị reject |
| AutoGain / EchoCancel / NoiseSuppress | **tắt** | Chỉnh cho tiếng nói, phá vân tay nhạc — xem [quyết định #1](../docs/kien-truc.md) |
| Chuẩn hoá đỉnh trước khi gửi | **có** (`WavNormalizer`) | Độ lợi micro Android không ổn định; mirror bước `signal_to_wav_bytes` của desktop |

## Lỗi thường gặp

**App báo "Cần cấp quyền micro"**. Vào Settings hệ thống bật micro cho
app (Android) hoặc Shazam (iOS).

**App báo "Không kết nối được máy chủ"**. Backend chưa chạy, hoặc ô
API URL sai. Trỏ về `http://10.0.2.2:8000` (emulator) hoặc IP LAN của
máy backend, bấm **Bắt đầu nhận diện** lại.

**Thu xong mà báo "Không nhận ra"**. Đúng như thiết kế — bài đó
không nằm trong kho. Đưa máy lại gần loa, tăng âm lượng, hoặc chạy
`shazam build --source local` sau khi chép file vào `data/songs/`.

**Server trả "Đoạn audio quá ngắn"**. Micro mở nhưng không nhận tín
hiệu (nhầm thiết bị vào, mic bị mute cứng). Kiểm tra trong Settings âm
thanh của hệ điều hành.

**Server trả "Không đọc được tệp âm thanh"**. File WAV không hợp lệ
— lỗi hiếm, thường chỉ xảy ra nếu thiết bị dừng ghi giữa chừng.

## Cùng với web & desktop

Backend FastAPI phục vụ cả ba client. Không cần đổi gì khi thêm
mobile client mới — chỉ cần trỏ `API_BASE_URL` đúng và có mạng tới
server.

```bash
# Terminal 1: backend + DB
docker compose up -d

# Terminal 2: web
cd web && pnpm dev                     # http://localhost:5173

# Terminal 3: desktop
python desktop-app/app.py             # http://127.0.0.1:8000

# Terminal 4: mobile (Android emulator)
cd mobile-app && flutter run
```

## Lệnh test

```bash
flutter analyze      # lint + type check
flutter test         # unit test cho parser
flutter pub deps     # in cây dependency
```
