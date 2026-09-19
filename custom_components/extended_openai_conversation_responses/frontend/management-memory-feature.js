export * from "./guest-mode-ui.js";
export * from "./management-temporary-memory.js";
import {reconcilePersistentMemories, hasPersistentMemoryCollection} from "./guest-mode-ui.js";
import {reconcileTemporaryMemories, hasTemporaryMemoryCollection} from "./management-temporary-memory.js";

export function reconcileMemories(panel) {
  return panel._memoryKind === "temporary" ? reconcileTemporaryMemories(panel) : reconcilePersistentMemories(panel);
}

export function hasMemoryCollection(panel) {
  return panel._memoryKind === "temporary" ? hasTemporaryMemoryCollection(panel) : hasPersistentMemoryCollection(panel);
}
