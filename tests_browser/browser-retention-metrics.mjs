// Overnight Chromium retention diagnostics. Compare equivalent post-GC states.
export function retentionGrowth(samples) {
  if (samples.length < 8) throw new Error("At least eight retained-runtime samples required");
  const median = values => [...values].sort((a, b) => a - b)[Math.floor(values.length / 2)];
  const limits = {heap_used: [8 * 1024 * 1024, 0.5], nodes: [1000, 0.5], documents: [5, 0.5], listeners: [100, 0.5]};
  return Object.entries(limits).flatMap(([metric, [absolute, relative]]) => {
    const values = samples.map(sample => sample[metric]);
    if (values.some(value => !Number.isFinite(value) || value < 0)) throw new Error(`Invalid ${metric} retention evidence`);
    const early = median(values.slice(0, 3)), late = median(values.slice(-3));
    const increasing = values.slice(1).filter((value, index) => value > values[index]).length;
    return late - early > absolute && late - early > early * relative && increasing >= Math.ceil((values.length - 1) * 0.7)
      ? [{metric, early, late, increasing, samples: values.length}] : [];
  });
}

export async function sampleRetainedRuntime(session) {
  // Two GC rounds let cross-heap DOM finalizers release references before sampling.
  await session.send("HeapProfiler.collectGarbage");
  await session.send("HeapProfiler.collectGarbage");
  const [heap, dom] = await Promise.all([session.send("Runtime.getHeapUsage"), session.send("Memory.getDOMCounters")]);
  return {heap_used: heap.usedSize, nodes: dom.nodes, documents: dom.documents, listeners: dom.jsEventListeners};
}
