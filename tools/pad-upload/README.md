# pad-upload

Uploads a key mapping to the CH57x USB macro pad (VID 0x1189 / PID 0x8840) over its vendor HID
interface. The protocol comes from ch57x-keyboard-tool. `final.json` is the live mapping
(button 0 = Pause for push-to-talk); `markers.json` is a test mapping (one letter per key) for
finding out which physical key is which.

**Warning:** this rewrites the pad's stored key bindings. The pad keeps them after you unplug it.

Install (separate from the app's requirements):

```
pip install -r tools/pad-upload/requirements.txt
```

Upload the live mapping (pad plugged in):

```
python tools/pad-upload/ch57x_upload.py tools/pad-upload/final.json
```
