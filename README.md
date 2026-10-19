# yt-download

Frontend ringan dengan processor media lokal. URL file audio/video langsung dari sumber yang mengizinkan unduhan dapat diperiksa, dikonversi menjadi MP4 atau MP3, dipotong pada rentang waktu tertentu, lalu diunduh.

## Jalankan di komputer sendiri

Butuh Python 3.10 atau lebih baru serta ffmpeg dan ffprobe yang tersedia di PATH. Tidak ada dependensi Python tambahan, instalasi npm, atau build frontend.

```powershell
python server.py
```

Buka http://127.0.0.1:8765. Jangan buka index.html langsung untuk menggunakan konversi; frontend perlu dilayani oleh processor lokal agar dapat berbicara ke API yang sama.

Tempel URL file media langsung atau pilih file lokal, lalu setujui pemrosesan. Pemeriksaan berjalan otomatis. Opsi format dan kualitas berasal dari file yang benar-benar diterima dan encoder FFmpeg yang terdeteksi. Pilih seluruh durasi atau rentang khusus, kemudian **Konversi & unduh**.

Batas unggahan awal adalah 2 GB. Untuk mengubahnya, jalankan python server.py --max-upload-mb 4096. Jika port 8765 sedang dipakai, pilih port lain dengan python server.py --port 8766.

## Kemampuan processor

- **MP4:** tersedia untuk file dengan trek video jika encoder H.264 libx264 ada. File yang memiliki audio juga memerlukan encoder AAC. Resolusi yang ditawarkan tidak lebih tinggi dari sumber; ukuran asli selalu tersedia.
- **MP3:** tersedia untuk file dengan trek audio jika encoder libmp3lame ada. Pilihan bitrate yang ditampilkan hanya muncul ketika encoder tersebut tersedia.
- **Rentang khusus:** HH:MM:SS divalidasi di frontend dan backend. Durasi dibaca dari media menggunakan ffprobe; rentang dinonaktifkan jika durasi tidak diketahui dan ditolak jika melewati durasi sumber.
- **Pratinjau dan status:** thumbnail dibuat dari frame file oleh FFmpeg. Durasi dan trek dibaca oleh ffprobe. Progres berasal dari proses FFmpeg; tautan unduh baru muncul setelah file hasil nyata tersedia.

FFmpeg dijalankan dengan argumen proses terpisah (tanpa shell). API hanya menerima permintaan dari loopback dan memeriksa origin lokal. File sumber dan hasil disimpan sementara di direktori temp, tidak dikirim ke server lain, dan dibersihkan saat server.py dihentikan. Hentikan server dengan Ctrl+C setelah selesai.

## URL sumber

URL harus mengarah langsung ke file audio atau video HTTP(S) yang dapat diunduh dan yang Anda berhak gunakan. Server menolak URL dengan kredensial, host jaringan privat secara default, respons halaman HTML, serta domain YouTube. Processor tidak mengambil stream platform video atau melewati DRM, access control, atau pembatasan sumber. Untuk pengujian lokal saja, `--allow-private-url-sources` dapat dipakai agar fixture `127.0.0.1` diterima.

## API lokal

- GET /api/health melaporkan runtime dan batas unggahan.
- POST /api/analyze menerima file lokal sebagai application/octet-stream, nama yang di-URL-encode pada header X-Upload-Filename, dan header X-Processing-Permission: confirmed setelah pengguna menyatakan berhak memprosesnya. Respons berisi durasi, trek, format yang tersedia, dan opsi resolusi/bitrate nyata.
- POST /api/analyze-url menerima JSON `{ "url": "https://…", "permissionConfirmed": true }`, mengunduh file media langsung secara sementara, lalu mengembalikan metadata dan opsi nyata yang sama. Respons hanya diterima jika content type atau ekstensi menunjukkan media yang didukung.
- GET /api/thumbnails/{sourceId} mengirim pratinjau JPEG yang dibuat dari frame file lokal, jika file video mendukungnya.
- POST /api/jobs menerima sourceId, format, opsi kualitas, dan range opsional { "startSeconds": 5, "endSeconds": 20 }. Respons 202 berisi ID pekerjaan.
- GET /api/jobs/{jobId} mengembalikan status/progres aktual. Saat status complete, respons memuat URL unduhan lokal.
- GET /api/download/{jobId} mengirim file hasil nyata sebagai unduhan.

Server hanya mengikat 127.0.0.1; API lokal ini dibuat untuk penggunaan satu orang pada komputer yang sama, bukan untuk dipublikasikan ke internet.

## Tes

Jalankan tes validasi frontend dan unit processor:

```powershell
node --test
python -m unittest discover -s tests -v
```

Tes konversi end-to-end membutuhkan FFmpeg dan ffprobe. Gunakan hanya file contoh yang Anda miliki izin untuk proses.
