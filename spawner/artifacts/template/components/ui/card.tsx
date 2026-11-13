import type { ComponentProps } from "react";
import { cn } from "@/lib/cn";

// A panel: Card > CardHeader (CardTitle, CardDescription) > CardContent > CardFooter
export function Card({ className, ...props }: ComponentProps<"div">) {
  return <div className={cn("flex flex-col gap-4 rounded-xl border bg-card py-5 shadow-xs", className)} {...props} />;
}

export function CardHeader({ className, ...props }: ComponentProps<"div">) {
  return <div className={cn("flex flex-col gap-1 px-5", className)} {...props} />;
}

export function CardTitle({ className, ...props }: ComponentProps<"h3">) {
  return <h3 className={cn("leading-tight font-semibold", className)} {...props} />;
}

export function CardDescription({ className, ...props }: ComponentProps<"p">) {
  return <p className={cn("text-sm text-muted-foreground", className)} {...props} />;
}

export function CardContent({ className, ...props }: ComponentProps<"div">) {
  return <div className={cn("px-5", className)} {...props} />;
}

export function CardFooter({ className, ...props }: ComponentProps<"div">) {
  return <div className={cn("flex items-center gap-2 px-5", className)} {...props} />;
}
