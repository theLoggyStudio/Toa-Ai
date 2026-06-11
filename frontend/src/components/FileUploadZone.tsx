import { useCallback } from 'react';
import { useDropzone } from 'react-dropzone';
import iconImg from '../assets/icon.png';

interface FileUploadZoneProps {
  files: File[];
  onFilesChange: (files: File[]) => void;
  disabled?: boolean;
}

export function FileUploadZone({
  files,
  onFilesChange,
  disabled = false,
}: FileUploadZoneProps) {
  const onDrop = useCallback(
    (accepted: File[]) => {
      const merged = [...files, ...accepted];
      const seen = new Set<string>();
      const unique = merged.filter((file) => {
        const key = `${file.name}:${file.size}:${file.lastModified}`;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      });
      onFilesChange(unique);
    },
    [files, onFilesChange],
  );

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    accept: {
      'image/png': ['.png'],
      'image/jpeg': ['.jpg', '.jpeg'],
    },
    disabled,
  });

  const removeFile = (index: number) => {
    onFilesChange(files.filter((_, i) => i !== index));
  };

  return (
    <div>
      <div
        {...getRootProps()}
        className={`toa-dropzone p-5 text-center ${
          isDragActive ? 'toa-dropzone-active' : ''
        } ${disabled ? 'opacity-50' : 'cursor-pointer'}`}
      >
        <input {...getInputProps()} />
        <img
          src={iconImg}
          alt=""
          className="toa-pixel-img toa-dropzone-icon mb-3"
        />
        <p className="mb-0">
          {isDragActive
            ? 'Déposez vos scans ici…'
            : 'Glissez-déposez vos pages (PNG, JPG, JPEG) ou cliquez pour parcourir'}
        </p>
      </div>

      {files.length > 0 && (
        <ul className="list-group mt-3">
          {files.map((file, index) => (
            <li
              key={`${file.name}-${index}`}
              className="list-group-item toa-list-item d-flex justify-content-between align-items-center"
            >
              <span>
                {file.name}{' '}
                <small className="toa-text-muted">
                  ({(file.size / 1024).toFixed(0)} Ko)
                </small>
              </span>
              {!disabled && (
                <button
                  type="button"
                  className="btn btn-sm toa-btn-outline"
                  onClick={() => removeFile(index)}
                >
                  Retirer
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
