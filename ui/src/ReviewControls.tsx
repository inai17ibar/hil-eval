import type { ReactNode } from "react";
import type { Status } from "./api";

export function ReviewControls({
  status,
  disabled,
  children,
}: {
  status: Status;
  disabled: boolean;
  children: ReactNode;
}) {
  return (
    <fieldset disabled={status === "superseded" || disabled}>
      {children}
    </fieldset>
  );
}
