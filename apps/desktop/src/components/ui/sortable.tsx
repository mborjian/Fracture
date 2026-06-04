import {
  DndContext,
  KeyboardSensor,
  MouseSensor,
  TouchSensor,
  closestCenter,
  type DragEndEvent,
  type UniqueIdentifier,
  useSensor,
  useSensors
} from "@dnd-kit/core";
import {
  SortableContext,
  arrayMove,
  rectSortingStrategy,
  sortableKeyboardCoordinates,
  useSortable
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { createContext, useContext, useMemo, type ButtonHTMLAttributes, type HTMLAttributes, type ReactNode } from "react";
import { cn } from "@/lib/cn";

type SortableRootContextValue<T> = {
  items: UniqueIdentifier[];
  getItemValue: (item: T) => UniqueIdentifier;
};

const SortableRootContext = createContext<SortableRootContextValue<unknown> | null>(null);

function useSortableRootContext<T>() {
  const context = useContext(SortableRootContext);
  if (!context) {
    throw new Error("Sortable components must be used inside Sortable");
  }
  return context as SortableRootContextValue<T>;
}

type SortableProps<T> = {
  value: T[];
  getItemValue: (item: T) => UniqueIdentifier;
  onValueChange?: (items: T[]) => void;
  onMove?: (items: T[], event: DragEndEvent) => void;
  children: ReactNode;
};

export function Sortable<T>({ value, getItemValue, onValueChange, onMove, children }: SortableProps<T>) {
  const sensors = useSensors(
    useSensor(MouseSensor, { activationConstraint: { distance: 6 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 150, tolerance: 8 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates })
  );

  const items = useMemo(() => value.map((item) => getItemValue(item)), [getItemValue, value]);

  const contextValue = useMemo<SortableRootContextValue<unknown>>(
    () => ({
      items,
      getItemValue: getItemValue as (item: unknown) => UniqueIdentifier
    }),
    [getItemValue, items]
  );

  const handleDragEnd = (event: DragEndEvent) => {
    const { active, over } = event;
    if (!over || active.id === over.id) return;

    const oldIndex = items.findIndex((item) => item === active.id);
    const newIndex = items.findIndex((item) => item === over.id);
    if (oldIndex < 0 || newIndex < 0) return;

    const nextItems = arrayMove(value, oldIndex, newIndex);
    onValueChange?.(nextItems);
    onMove?.(nextItems, event);
  };

  return (
    <SortableRootContext.Provider value={contextValue}>
      <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
        {children}
      </DndContext>
    </SortableRootContext.Provider>
  );
}

type SortableContentProps = HTMLAttributes<HTMLDivElement>;

export function SortableContent<T>({ children, ...props }: SortableContentProps) {
  const context = useSortableRootContext<T>();

  return (
    <SortableContext items={context.items} strategy={rectSortingStrategy}>
      <div {...props}>{children}</div>
    </SortableContext>
  );
}

type SortableItemContextValue = ReturnType<typeof useSortable> & {
  disabled?: boolean;
};

const SortableItemContext = createContext<SortableItemContextValue | null>(null);

function useSortableItemContext() {
  const context = useContext(SortableItemContext);
  if (!context) {
    throw new Error("SortableItemHandle must be used inside SortableItem");
  }
  return context;
}

type SortableItemProps<T> = HTMLAttributes<HTMLDivElement> & {
  value: T;
  disabled?: boolean;
};

export function SortableItem<T>({ value, disabled, className, style, children, ...props }: SortableItemProps<T>) {
  const context = useSortableRootContext<T>();
  const sortable = useSortable({
    id: context.getItemValue(value),
    disabled
  });

  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = sortable;
  const itemContextValue = useMemo<SortableItemContextValue>(
    () => ({
      ...sortable,
      disabled
    }),
    [disabled, sortable]
  );

  return (
    <SortableItemContext.Provider value={itemContextValue}>
      <div
        ref={setNodeRef}
        style={{
          transform: CSS.Transform.toString(transform),
          transition,
          ...style
        }}
        className={cn("touch-manipulation", isDragging ? "z-10 opacity-70" : "", className)}
        data-dragging={isDragging ? "" : undefined}
        {...props}
      >
        {children}
      </div>
    </SortableItemContext.Provider>
  );
}

type SortableItemHandleProps = ButtonHTMLAttributes<HTMLButtonElement>;

export function SortableItemHandle({ className, onClick, ...props }: SortableItemHandleProps) {
  const { attributes, listeners, setActivatorNodeRef, isDragging, disabled } = useSortableItemContext();

  return (
    <button
      type="button"
      ref={setActivatorNodeRef}
      className={cn(
        "inline-flex items-center justify-center rounded-md text-textMuted transition-colors hover:text-text",
        disabled ? "cursor-not-allowed opacity-50" : isDragging ? "cursor-grabbing" : "cursor-grab",
        className
      )}
      onClick={(event) => {
        event.stopPropagation();
        onClick?.(event);
      }}
      {...attributes}
      {...listeners}
      {...props}
    />
  );
}
