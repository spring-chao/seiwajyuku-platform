import { http } from "@/utils/http";

export type UserResult = {
  success: boolean;
  data: {
    avatar: string;
    username: string;
    nickname: string;
    roles: Array<string>;
    permissions: Array<string>;
    accessToken: string;
    refreshToken: string;
    expires: Date;
  };
};

export type RefreshTokenResult = {
  success: boolean;
  data: {
    accessToken: string;
    refreshToken: string;
    expires: Date;
  };
};

type LoginResponse = {
  success: boolean;
  data: {
    access_token: string;
    refresh_token: string;
    expires_in: number;
  };
};

type MeResponse = {
  success: boolean;
  data: {
    username: string;
    display_name: string;
    roles: string[];
    permissions: string[];
  };
};

type TransientRequestError = {
  code?: string;
  response?: { status?: number };
};

function isTransientRequestError(error: unknown): boolean {
  const requestError = error as TransientRequestError;
  const status = requestError?.response?.status;
  if (!status) return true;
  if (status === 401 || (status && status < 500)) return false;
  return (
    [408, 425, 429, 500, 502, 503, 504].includes(status) ||
    requestError.code === "ECONNABORTED" ||
    requestError.code === "ERR_NETWORK"
  );
}

async function withLoginRetry<T>(request: () => Promise<T>): Promise<T> {
  try {
    return await request();
  } catch (error) {
    if (!isTransientRequestError(error)) throw error;
    // CloudRun/数据库首次唤醒可能只需要几百毫秒；自动重试一次，避免用户重复点击。
    await new Promise(resolve => setTimeout(resolve, 600));
    return request();
  }
}

export const getLogin = async (data?: {
  username?: string;
  password?: string;
}): Promise<UserResult> => {
  const { login, me } = await withLoginRetry(async () => {
    const login = await http.request<LoginResponse>(
      "post",
      "/api/v1/auth/login",
      { data, timeout: 20000 }
    );
    const me = await http.request<MeResponse>("get", "/api/v1/me", {
      headers: {
        Authorization: `Bearer ${login.data.access_token}`
      },
      timeout: 20000
    });
    return { login, me };
  });
  return {
    success: login.success && me.success,
    data: {
      avatar: "",
      username: me.data.username,
      nickname: me.data.display_name,
      roles: me.data.roles,
      permissions: me.data.permissions,
      accessToken: login.data.access_token,
      refreshToken: login.data.refresh_token,
      expires: new Date(Date.now() + login.data.expires_in * 1000)
    }
  };
};

export const refreshTokenApi = async (data?: {
  refreshToken?: string;
}): Promise<RefreshTokenResult> => {
  const result = await http.request<{
    success: boolean;
    data: { access_token: string; expires_in: number };
  }>("post", "/api/v1/auth/refresh", {
    data: { refresh_token: data?.refreshToken }
  });
  return {
    success: result.success,
    data: {
      accessToken: result.data.access_token,
      refreshToken: data?.refreshToken ?? "",
      expires: new Date(Date.now() + result.data.expires_in * 1000)
    }
  };
};
