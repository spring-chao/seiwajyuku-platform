import { http } from "@/utils/http";

export type GroupRoster = {
  class_name: string;
  groups: { id: string; name: string }[];
  members: { id: number; member_code: string; name: string; group_org_unit_id: string | null; group_name: string; unavailable?: boolean }[];
};
export type GroupReassignment = { class_org_unit_id: string; assignments: { member_id: number; group_org_unit_id: string }[] };
export type GroupReassignmentPreview = {
  preview_fingerprint: string;
  changed_count: number;
  rows: { member_id: number; member_code: string; name: string; old_group_name: string; new_group_name: string; changed: boolean }[];
};
export const getGroupReassignmentRoster = (class_org_unit_id: string) =>
  http.request<{ success: boolean; data: GroupRoster }>("get", "/api/v1/members/group-reassignment/roster", { params: { class_org_unit_id } });
export const previewGroupReassignment = (data: GroupReassignment) =>
  http.request<{ success: boolean; data: GroupReassignmentPreview }>("post", "/api/v1/members/group-reassignment/preview", { data });
export const applyGroupReassignment = (data: GroupReassignment & { preview_fingerprint: string; reason: string }) =>
  http.request<{ success: boolean; data: { changed_count: number } }>("post", "/api/v1/members/group-reassignment/apply", { data });
