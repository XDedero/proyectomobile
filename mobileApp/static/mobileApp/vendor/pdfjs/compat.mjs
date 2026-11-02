// Run in both the window and the PDF worker before loading PDF.js.
// Some Safari versions lack this API even though they support module workers.
if (typeof Promise.withResolvers !== 'function') {
    Object.defineProperty(Promise, 'withResolvers', {
        configurable: true,
        writable: true,
        value: function withResolvers() {
            let resolve, reject;
            const promise = new this((resolvePromise, rejectPromise) => {
                resolve = resolvePromise;
                reject = rejectPromise;
            });
            return { promise, resolve, reject };
        },
    });
}
