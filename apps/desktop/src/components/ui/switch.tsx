import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/cn";

const switchVariants = cva(
  "relative inline-flex shrink-0 items-center rounded-full border border-white/10 transition-colors outline-none focus-visible:ring-2 focus-visible:ring-accent/50 disabled:cursor-not-allowed disabled:opacity-50",
  {
    variants: {
      size: {
        default: "h-5 w-9",
        sm: "h-4 w-7"
      },
      checked: {
        true: "bg-accent",
        false: "bg-panelAlt"
      }
    },
    defaultVariants: {
      size: "default",
      checked: false
    }
  }
);

const thumbVariants = cva(
  "pointer-events-none inline-block rounded-full bg-white shadow-sm transition-transform",
  {
    variants: {
      size: {
        default: "h-4 w-4",
        sm: "h-3 w-3"
      },
      checked: {
        true: "",
        false: ""
      }
    },
    defaultVariants: {
      size: "default",
      checked: false
    }
  }
);

export interface SwitchProps
  extends Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, "onChange">,
    VariantProps<typeof switchVariants> {
  checked?: boolean;
  onCheckedChange?: (checked: boolean) => void;
}

export const Switch = React.forwardRef<HTMLButtonElement, SwitchProps>(
  ({ className, size, checked = false, onCheckedChange, disabled, ...props }, ref) => (
    <button
      {...props}
      ref={ref}
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      className={cn(
        switchVariants({ size, checked, className }),
        "shadow-[0_0_0_1px_rgba(0,0,0,0.05),0_1px_3px_rgba(0,0,0,0.2)]"
      )}
      onClick={(event) => {
        props.onClick?.(event);
        if (!event.defaultPrevented && !disabled) {
          onCheckedChange?.(!checked);
        }
      }}
    >
      <span
        className={cn(thumbVariants({ size, checked }), checked ? "translate-x-[100%]" : "translate-x-0.5",
            "shadow-[0_0_0_1px_rgba(0,0,0,0.05),0_1px_3px_rgba(0,0,0,0.15)]")}
      />
    </button>
  )
);

Switch.displayName = "Switch";
