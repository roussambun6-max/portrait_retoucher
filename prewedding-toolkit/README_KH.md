# Prewedding Toolkit (កែរូប + កាត់វីដេអូ)

កម្មវិធីនេះមាន **២ ផ្នែក**។ វាដំណើរការដោយមិនត្រូវការអ៊ីនធឺណិតទេ បន្ទាប់ពីដំឡើងរួច។

| ឯកសារ | ការងារ |
|---|---|
| `retouch.bat` / `retouch_photos.py` | កែរូប prewedding ច្រើនសន្លឹកក្នុងពេលតែមួយ |
| `cut_video.bat` / `cut_video.py` | កាត់វីដេអូ prewedding |

---

## ១. ដំឡើងលើកុំព្យូទ័រថ្មី (ធ្វើតែម្តងគត់)

1. **ដំឡើង Python 3.10 ឬថ្មីជាងនេះ** ពី https://www.python.org/downloads/
   ⚠️ ពេលដំឡើង ត្រូវធីក ✅ **"Add python.exe to PATH"**
2. **Extract (Unzip)** ឯកសារ `prewedding-toolkit.zip` ទៅទីតាំងណាមួយ ឧ. `D:\prewedding-toolkit`
3. ចុចពីរដងលើ **`setup.bat`** ហើយរង់ចាំវាដំឡើងរួចរាល់ (ពេលនេះត្រូវការអ៊ីនធឺណិត ប្រហែល ២-៥ នាទី)

មិនចាំបាច់ដំឡើង ffmpeg ដោយឡែកទេ ព្រោះវាភ្ជាប់មកជាមួយស្រាប់។

> **macOS / Linux៖** បើក Terminal ក្នុង folder នេះ ហើយវាយ `./setup.sh`

---

## ២. កែរូប Prewedding

**វិធីងាយ៖** **អូស (drag) folder រូបថត** ទៅដាក់លើ `retouch.bat`។ ឬចុចពីរដងលើ `retouch.bat` រួចអូស folder ទៅក្នុងផ្ទាំងខ្មៅ ហើយចុច Enter។

បន្ទាប់មក វានឹងសួរ៖
- **Skin smoothing** (ធ្វើឲ្យស្បែករលោង)៖ 0–100។ ចុច Enter ដើម្បីប្រើតម្លៃដើម 40។
- **Crop ratio**៖ ឧ. `4:5` សម្រាប់ Facebook/Instagram, `3:2` សម្រាប់បោះពុម្ព។ ចុច Enter ដើម្បីទុកទំហំដើម។

រូបដែលកែរួចនឹង save ក្នុង folder **`_retouched`** នៅក្នុង folder រូបដើម។ រូបដើមមិនត្រូវបានកែប្រែទេ។

**អ្វីដែលវាធ្វើលើរូបនីមួយៗ៖**
1. កែ white balance (ពណ៌លឿង ឬខៀវខ្លាំងពេក)
2. កែពន្លឺ និង contrast ដោយស្វ័យប្រវត្តិ
3. ធ្វើឲ្យស្បែករលោង តែកន្លែងមុខ និងស្បែកប៉ុណ្ណោះ ដោយរក្សាវាយនភាពស្បែក
4. ធ្វើឲ្យច្បាស់ (sharpen) កន្លែងដែលមិនមែនជាស្បែក ឧ. សក់ សម្លៀកបំពាក់ និងផ្កា
5. Crop តាម ratio ដោយរក្សាមុខនៅកណ្តាល
6. Save ជា JPG គុណភាព 95

**ប្រើក្នុង Command Prompt (កម្រិតខ្ពស់)៖**
```
cd /d D:\prewedding-toolkit
.venv\Scripts\python retouch_photos.py "D:\Photos\Couple" --smooth 50 --ratio 4:5 --long-edge 2048
```
| ជម្រើស | ន័យ |
|---|---|
| `-o "D:\Out"` | folder សម្រាប់ save |
| `--smooth 0-100` | កម្រិតធ្វើឲ្យស្បែករលោង (ដើម 40) |
| `--wb 0-1` | កម្លាំងកែ white balance (ដើម 0.6) |
| `--contrast 0-1` | contrast (ដើម 0.5) |
| `--sharpen 0-1` | ភាពច្បាស់ (ដើម 0.35) |
| `--ratio 4:5` | crop តាម ratio |
| `--long-edge 2048` | បង្រួមទំហំ (សម្រាប់ Facebook ប្រើ 2048) |

---

## ៣. កាត់វីដេអូ Prewedding

**វិធីងាយ៖** **អូសវីដេអូ** ទៅដាក់លើ `cut_video.bat` ហើយឆ្លើយសំណួរ៖
- **Start time**៖ ឧ. `0:30` (ចុច Enter ដើម្បីចាប់ផ្តើមពីដើម)
- **End time**៖ ឧ. `2:15` (ចុច Enter ដើម្បីយករហូតដល់ចប់)
- **Ratio**៖ `16:9` (YouTube), `9:16` (TikTok/Reels), `1:1`, `4:5`
- **Fade**៖ ឧ. `0.5` ដើម្បីឲ្យរូបភាពចូល និងចេញបន្តិចម្តងៗ

វីដេអូដែលកាត់រួចនឹង save ក្បែរវីដេអូដើម ជាឈ្មោះ **`ឈ្មោះដើម_cut.mp4`**។

**ប្រើក្នុង Command Prompt (កម្រិតខ្ពស់)៖**
```
cd /d D:\prewedding-toolkit

:: កាត់មួយផ្នែក
.venv\Scripts\python cut_video.py "D:\Videos\pw01.mp4" --start 0:30 --end 2:15

:: កាត់ច្រើនផ្នែក ហើយភ្ជាប់ជា highlight តែមួយ
.venv\Scripts\python cut_video.py "D:\Videos\pw01.mp4" --segments "0:30-0:45, 1:10-1:25, 3:00-3:20" --fade 0.5

:: វីដេអូបញ្ឈរ 1080x1920 សម្រាប់ TikTok
.venv\Scripts\python cut_video.py "D:\Videos\pw01.mp4" --start 1:00 --end 1:30 --ratio 9:16 --height 1920

:: កាត់លឿនបំផុត ដោយមិន encode ថ្មី (ចំណុចកាត់អាចខុសបន្តិច)
.venv\Scripts\python cut_video.py "D:\Videos\pw01.mp4" --start 0:30 --end 2:15 --fast
```
| ជម្រើស | ន័យ |
|---|---|
| `--start` / `--end` | ពេលចាប់ផ្តើម / បញ្ចប់ (`90`, `1:30`, `01:01:30`) |
| `--segments` | ផ្នែកច្រើនដែលត្រូវភ្ជាប់គ្នា |
| `--ratio` | crop តាម ratio (យកផ្នែកកណ្តាល) |
| `--height` | កម្ពស់វីដេអូចេញ ឧ. 1080, 1920 |
| `--fade` | fade in/out គិតជាវិនាទី |
| `--mute` | លុបសំឡេង |
| `--crf 15-28` | គុណភាព (លេខតូចមានគុណភាពល្អ តែ file ធំ។ ដើម 18) |
| `-o file.mp4` | ឈ្មោះ file ចេញ |

---

## បញ្ហាដែលអាចជួប

- **"Python not found"**៖ ដំឡើង Python ម្តងទៀត ហើយធីក "Add python.exe to PATH"។
- **"Please run setup.bat first"**៖ ចុចពីរដងលើ `setup.bat` ជាមុនសិន។
- **ផ្លាស់ទៅកុំព្យូទ័រថ្មី**៖ កុំចម្លង folder `.venv`។ ត្រូវចម្លងតែ zip ហើយដំណើរការ `setup.bat` ម្តងទៀតនៅលើកុំព្យូទ័រថ្មី។
