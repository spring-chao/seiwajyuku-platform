function resolveVolunteerServices(data) {
  const serviceData = data || {};
  const roles = Array.isArray(serviceData.roles) ? serviceData.roles : [];
  return {
    roles,
    isVolunteer: Boolean(serviceData.is_volunteer || roles.length),
    canManageStudyMeeting: roles.some(item =>
      Array.isArray(item.capabilities) && item.capabilities.includes("STUDY_MEETING_MANAGE")
    ),
    serviceAssignments: roles.map(item => ({
      key: `${item.position_key || item.position_name || "volunteer"}-${item.scope_org_unit_id || item.org_unit_id || "default"}`,
      positionName: item.position_name || "志工",
      scopeName: item.scope_name || "服务范围暂未记录",
      capabilities: item.capabilities || []
    }))
  };
}

module.exports = { resolveVolunteerServices };
