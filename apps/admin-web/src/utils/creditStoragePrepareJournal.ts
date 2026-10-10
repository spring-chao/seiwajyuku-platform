type StoragePort = Pick<Storage, "getItem" | "setItem">;
export function creditStoragePrepareKey(version: string) {
  if (!["0064", "0065", "0066", "0067"].includes(version)) {
    throw new Error("不支持的结算存储准备步骤");
  }
  return `credit-storage-prepare-${version}`;
}
export async function submitCreditStoragePrepareOnce<T>(
  storage: StoragePort,
  version: string,
  record: { commit: string; fingerprint: string; submitted_at: string },
  dispatch: () => Promise<T>
): Promise<T> {
  const key = creditStoragePrepareKey(version);
  if (storage.getItem(key) !== null) {
    throw new Error("本步骤已提交，请刷新核验；不重复发送。");
  }
  storage.setItem(key, JSON.stringify(record));
  return dispatch();
}
