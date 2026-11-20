import React from 'react';

export default function PropertyCard({ property }) {
  if (!property) return null;

  return (
    <div className="bg-[#FFFFFF] border border-[#E0E0E0] p-4 text-[#0F172A] w-full max-w-sm flex flex-col justify-between">
      <div>
        <div className="flex justify-between items-center text-xs font-mono text-neutral-500 uppercase mb-2">
          <span className="border border-neutral-300 px-1.5 py-0.5">{property.source || 'DarGlobal'}</span>
          <span>ID: {property.id || 'N/A'}</span>
        </div>
        <h3 className="font-semibold text-base mb-1 line-clamp-1">{property.title}</h3>
        <p className="text-xs text-neutral-600 mb-3">{property.location}</p>

        <div className="grid grid-cols-2 gap-2 text-xs font-mono bg-neutral-50 p-2 border border-[#E0E0E0] mb-3">
          <div>
            <span className="text-neutral-500 block">PRICE</span>
            <span className="font-bold text-[#2563EB]">
              {property.price_formatted || 'Inquire Price'}
            </span>
          </div>
          <div>
            <span className="text-neutral-500 block">AREA</span>
            <span className="font-bold">
              {property.area_sqft ? `${property.area_sqft.toLocaleString()} SQFT` : 'N/A'}
            </span>
          </div>
          {property.bedrooms && (
            <div>
              <span className="text-neutral-500 block">BEDROOMS</span>
              <span className="font-bold">{property.bedrooms} BD</span>
            </div>
          )}
          {property.bathrooms && (
            <div>
              <span className="text-neutral-500 block">BATHROOMS</span>
              <span className="font-bold">{property.bathrooms} BA</span>
            </div>
          )}
        </div>

        {property.amenities && property.amenities.length > 0 && (
          <div className="flex flex-wrap gap-1 mb-3">
            {property.amenities.slice(0, 3).map((amenity, idx) => (
              <span
                key={idx}
                className="text-[10px] font-mono border border-[#E0E0E0] px-1.5 py-0.5 bg-white text-neutral-600"
              >
                {amenity}
              </span>
            ))}
          </div>
        )}
      </div>

      {property.source_url && (
        <a
          href={property.source_url}
          target="_blank"
          rel="noopener noreferrer"
          className="block text-center text-xs font-mono font-semibold uppercase py-2 bg-[#0F172A] text-white hover:bg-[#2563EB] transition-colors border border-[#0F172A]"
        >
          View Listing
        </a>
      )}
    </div>
  );
}
