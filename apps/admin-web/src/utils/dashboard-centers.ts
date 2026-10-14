type Center = {
  id: string;
  name: string;
  active_member_count: number;
  shuku_org_unit_id?: string | null;
};
type Shuku = { id: string; name: string };

export function centerScope(
  centers: Center[],
  shukus: Shuku[],
  selected: string
) {
  const options = [...shukus];
  if (centers.some(center => !center.shuku_org_unit_id)) {
    options.push({ id: "unassigned", name: "归属待核" });
  }
  const selectedId = options.some(option => option.id === selected)
    ? selected
    : options.find(option => option.id === "org-suzhou")?.id ||
      options[0]?.id ||
      "";
  const rows = centers.filter(center =>
    selectedId === "unassigned"
      ? !center.shuku_org_unit_id
      : !!selectedId && center.shuku_org_unit_id === selectedId
  );
  return {
    options,
    selectedId,
    name:
      options.find(option => option.id === selectedId)?.name ||
      "暂无可见二级塾",
    rows,
    total: rows.reduce((total, center) => total + center.active_member_count, 0)
  };
}
