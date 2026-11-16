// Serialize automatic and manual translations within this browser tab.
let tail = Promise.resolve();
export class LiveTranslation {
  constructor({ run, valid, notify = () => {}, delay = 800 }) {
    Object.assign(this, { run, valid, notify, delay });
    this.revision = 0;
    this.timer = null;
    this.disposed = false;
  }
  cancel() { clearTimeout(this.timer); this.timer = null; this.revision++; }
  schedule(immediate = false) {
    this.cancel();
    const revision = this.revision;
    this.notify();
    const enqueue = () => {
      this.timer = null;
      tail = tail.catch(() => {}).then(async () => {
        if (!this.disposed && revision === this.revision && this.valid()) await this.run();
      }).catch(() => {});
    };
    if (immediate) enqueue();
    else this.timer = setTimeout(enqueue, this.delay);
  }
  dispose() { this.cancel(); this.disposed = true; }
}
