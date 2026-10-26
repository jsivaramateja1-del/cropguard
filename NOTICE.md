# Third-party notices

## Reference photos in the disease grid

`frontend/public/diseases/*.jpg` — one photograph per condition, 13 images.

- **Source:** PlantVillage dataset, Mohanty, Hughes & Salathé (2016), *Using Deep Learning
  for Image-Based Plant Disease Detection*, Frontiers in Plant Science 7:1419.
- **Licence:** the dataset's paper and its Hugging Face card state **CC BY-SA 3.0**. Note
  that the GitHub mirror carries no LICENSE file, and at least one downstream mirror
  (Meta-Album) records the original release as CC0 1.0. This project treats it as
  CC BY-SA 3.0, the more restrictive of the two.
- **Obligations:** attribution (above) and share-alike on the images themselves.

Rice conditions have no reference photo because PlantVillage does not include rice; the UI
shows an explicit "no reference photo" placeholder rather than a broken frame.

### A related question worth your attention

The paper adds that *algorithms trained on the data fall under the same licence*. The
CropGuard model was fine-tuned on PlantVillage. This repository is MIT-licensed. If you
intend to distribute the model or the application, take advice on whether CC BY-SA 3.0
attaches to the trained weights — it is a licensing question, not a technical one, and it
is outside what this file can settle.

## Weather data

Open-Meteo (CC BY 4.0), with MET Norway as fallback. Attributed in the interface footer.

## Interface libraries

Radix UI primitives, Tailwind CSS, Leaflet, Lucide icons — all MIT or BSD licensed.
