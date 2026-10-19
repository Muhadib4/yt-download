# yt-download

Downloader dan konverter media lokal untuk konten yang Anda miliki atau berhak unduh. Mendukung URL video YouTube publik melalui **yt-dlp**, URL file media langsung, serta file lokal. Output dapat berupa MP4 atau MP3, dengan rentang waktu khusus dan batas durasi hasil maksimal **12 jam**.

## Persyaratan

- Python 3.10+
- FFmpeg dan ffprobe tersedia di PATH
- yt-dlp terbaru untuk URL YouTube (opsional untuk file lokal / URL media langsung)

### Windows

1. Pasang FFmpeg dan pastikan perintah `ffmpeg -version` serta `ffprobe -version` bekerja di PowerShell.
2. Instal yt-dlp:

   ```powershell
   py -m pip install -r requirements.txt
   ```

3. Jalankan aplikasi:

   ```powershell
   py server.py
   ```

4. Buka http://127.0.0.1:8765.

Jika yt-dlp baru dipasang, mulai ulang server. Untuk memperbarui yt-dlp: `py -m pip install --upgrade yt-dlp`.

## Penggunaan

1. Tempel URL YouTube publik atau URL langsung file audio/video, atau pilih file lokal.
2. Centang konfirmasi bahwa Anda berhak mengunduh atau memproses konten tersebut.
3. Pilih MP4 atau MP3.
4. Untuk MP4, pilih resolusi yang tersedia dari sumber: 144p, 240p, 360p, 480p, 720p, 1080p, 1440p, 2160p, atau 4320p. Pilihan yang ditampilkan dibatasi resolusi yang benar-benar tersedia; video tidak di-upscale.
5. Pilih seluruh durasi atau rentang khusus dalam format HH:MM:SS. Durasi hasil maksimal 12 jam.
6. Klik **Konversi & unduh**. Progres akan ditampilkan dan tautan unduh muncul setelah file selesai dibuat.

Untuk video YouTube, metadata diperiksa lebih dahulu. File baru diunduh saat pekerjaan dimulai. Format video dan audio yang terpisah digabungkan/dikonversi dengan FFmpeg melalui yt-dlp. Arsip live stream yang sudah tersedia dapat bekerja seperti video biasa; live stream yang masih berlangsung dan tidak memiliki durasi tetap belum didukung.

## Batasan dan keamanan

- Gunakan hanya konten yang Anda miliki atau yang Anda punya izin untuk mengunduh. Hormati hak cipta dan ketentuan layanan platform.
- Aplikasi tidak memakai cookie, login, atau mekanisme untuk melewati DRM, paywall, atau pembatasan akses. Video privat, khusus anggota, atau konten yang memerlukan autentikasi tidak didukung.
- Resolusi maksimum bergantung pada sumber dan format yang tersedia. Memilih 4K/8K tidak akan menciptakan detail yang tidak ada pada sumber.
- File sementara disimpan lokal dan dibersihkan ketika server dihentikan. Jangan memublikasikan server ini ke internet; server hanya mengikat alamat loopback 127.0.0.1.
- URL media langsung tetap diperiksa untuk menolak host privat secara default. Domain YouTube diproses melalui jalur yt-dlp yang terpisah.

## API lokal

- `GET /api/health`: status FFmpeg, ffprobe, yt-dlp, dan batas unggahan.
- `POST /api/analyze`: periksa file lokal.
- `POST /api/analyze-url`: periksa metadata video YouTube atau unduh sementara URL file media langsung.
- `POST /api/jobs`: mulai pemrosesan dengan format, kualitas, dan rentang waktu.
- `GET /api/jobs/{jobId}`: status dan progres.
- `GET /api/download/{jobId}`: unduh file hasil.

## Tes

```powershell
node --test
py -m unittest discover -s tests -v
```
