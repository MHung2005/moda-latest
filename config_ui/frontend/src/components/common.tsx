import { ReactNode } from "react";
import { App } from "antd";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ApiError } from "../api";

/** Vietnamese label with the original config key underneath for cross-checking. */
export function FieldLabel({ label, field }: { label: ReactNode; field: string }) {
  return (
    <span>
      {label}
      <span className="field-key">{field}</span>
    </span>
  );
}

export function errorText(error: unknown): string {
  if (error instanceof ApiError) {
    return error.details.length > 1 ? `${error.message} (${error.details.slice(1).join("; ")})` : error.message;
  }
  return String(error);
}

/** Mutation that toasts success/failure and refreshes every query (incl. pending changes). */
export function useSave<TArgs, TResult>(fn: (args: TArgs) => Promise<TResult>, successText = "Đã lưu") {
  const { message } = App.useApp();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => {
      message.success(successText);
      void queryClient.invalidateQueries();
    },
    onError: (error) => message.error(errorText(error)),
  });
}

/** Triton model names found under models/triton, plus the values already in use. */
export function tritonOptions(found: string[] | undefined, current: unknown[]) {
  const names = new Set([...(found ?? []), ...current.filter(Boolean).map(String)]);
  return [...names].sort().map((value) => ({ value, label: value }));
}

export const PURPOSE_LABEL: Record<string, string> = {
  lane: "Làn xe",
  weight: "Màn hình cân",
};
