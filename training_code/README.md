# Training and generation record

This deterministic baseline uses no trained or fine-tuned models. No training,
data generation, or preprocessing for training was performed. The inference
parameter count is zero, and there are no checkpoints or adapters. The provided
train split is used only for local evaluation; it is not packaged in the image.

The inference implementation is in `/code/src/datamate/`. Run it with
`bash /code/predict.sh`; it reads the mounted split at `/data` and writes to
`/result`. The synthetic smoke fixture can be regenerated with
`python -m datamate smoke`. See `/code/docs/VALIDATION.md` for checks and
`/code/models/README.md` for the model declaration.

`data_manifest.json` records the absence of training inputs and artifacts.
