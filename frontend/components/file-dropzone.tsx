"use client";

import { DragEvent, ReactNode, useState } from "react";

type FileDropzoneProps = {
  accept: string;
  title: string;
  hint?: string;
  multiple?: boolean;
  disabled?: boolean;
  files?: readonly File[];
  showSelectedFiles?: boolean;
  className?: string;
  children?: ReactNode;
  onFilesSelected: (files: File[]) => void;
};

export default function FileDropzone({
  accept,
  title,
  hint = accept,
  multiple = false,
  disabled = false,
  files = [],
  showSelectedFiles = true,
  className = "",
  children,
  onFilesSelected,
}: FileDropzoneProps) {
  const [dragging, setDragging] = useState(false);

  function chooseFiles(fileList: FileList | null) {
    if (!fileList?.length || disabled) return;
    const selected = Array.from(fileList);
    onFilesSelected(multiple ? selected : selected.slice(0, 1));
  }

  function handleDrop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    setDragging(false);
    chooseFiles(event.dataTransfer.files);
  }

  function handleDragLeave(event: DragEvent<HTMLLabelElement>) {
    if (
      event.relatedTarget instanceof Node &&
      event.currentTarget.contains(event.relatedTarget)
    ) {
      return;
    }
    setDragging(false);
  }

  return (
    <label
      className={`file-dropzone${dragging ? " is-dragging" : ""}${disabled ? " is-disabled" : ""}${className ? ` ${className}` : ""}`}
      onDragEnter={(event) => {
        event.preventDefault();
        if (!disabled) setDragging(true);
      }}
      onDragOver={(event) => {
        event.preventDefault();
        event.dataTransfer.dropEffect = disabled ? "none" : "copy";
      }}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      <input
        className="file-dropzone-input"
        type="file"
        accept={accept}
        multiple={multiple}
        disabled={disabled}
        onChange={(event) => {
          chooseFiles(event.currentTarget.files);
          event.currentTarget.value = "";
        }}
      />
      {children ?? (
        <span className="file-dropzone-copy">
          <strong>{dragging ? "Thả tệp để tải lên" : title}</strong>
          <span>{hint} · Kéo thả vào đây hoặc nhấn để chọn</span>
          {showSelectedFiles && files.length > 0 && (
            <span className="file-dropzone-selected">
              {files.map((file) => file.name).join(", ")}
            </span>
          )}
        </span>
      )}
    </label>
  );
}
